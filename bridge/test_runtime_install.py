import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge import install_runtime as installer
from scripts.prepare_probe_apk import patch_bootstrap

OLD, NEW = "1" * 64, "2" * 64
BASE = "/data/app/test/nullsroyale.rel.free/base.apk"
RESOURCES = {**installer.CONTENT_LOCK["requiredFiles"], "fingerprint.json": installer.MANIFEST["runtime_fingerprint_sha256"]}


class PendingDownloadsTest(unittest.TestCase):
    def setUp(self):
        self.contents = {"sc/a.sc.update": b"complete asset", "sc/b.sc.update": b"another asset"}
        self.manifest = json.dumps({"files": [{"file": name.removesuffix(".update"),
                                              "sha": hashlib.sha1(data).hexdigest()}
                                             for name, data in self.contents.items()]})
        self.fingerprint = hashlib.sha256(self.manifest.encode()).hexdigest()
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.contents.items()}
        self.hashes["fingerprint.json"] = self.fingerprint
        self.operations = []
        self.device = type("Device", (), {"serial": "emulator-5556", "shell": self.shell})()
        lock = patch.dict(installer.CONTENT_LOCK, {"fingerprints": [self.fingerprint]})
        lock.start()
        self.addCleanup(lock.stop)

    def shell(self, script):
        self.operations.append(script)
        if script.startswith("cat "):
            return self.manifest
        if script.startswith("sha1sum "):
            name = script.split("/update/", 1)[1]
            return hashlib.sha1(self.contents[name]).hexdigest() + " " + name
        return ""

    def test_complete_assets_are_published_with_manifest_checks(self):
        with patch.object(installer, "file_hash", side_effect=lambda _, path: self.hashes[path.split("/update/", 1)[1]]
                          if path.endswith("fingerprint.json") else self.hashes[path.split("/update/", 1)[1] + ".update"]), \
                patch.object(installer, "save_receipt") as receipt:
            result = installer.recover_pending_downloads(self.device, self.hashes)
        self.assertEqual(result["sc/a.sc"], self.hashes["sc/a.sc.update"])
        self.assertEqual(len([op for op in self.operations if op.startswith("cp ")]), 2)
        receipt.assert_called_once()

    def test_one_incomplete_download_prevents_all_copies(self):
        self.contents["sc/b.sc.update"] = b"truncated"
        with patch.object(installer, "file_hash", return_value=self.fingerprint):
            with self.assertRaisesRegex(ValueError, "未完成"):
                installer.recover_pending_downloads(self.device, self.hashes)
        self.assertFalse(any(op.startswith("cp ") for op in self.operations))

    def test_conflicting_final_asset_is_preserved(self):
        self.hashes["sc/a.sc"] = "f" * 64
        with patch.object(installer, "file_hash", return_value=self.fingerprint):
            with self.assertRaisesRegex(ValueError, "冲突"):
                installer.recover_pending_downloads(self.device, self.hashes)
        self.assertFalse(any(op.startswith("cp ") for op in self.operations))


class FakeDevice:
    serial = "127.0.0.1:16384"

    def __init__(self):
        self.current = OLD
        self.resources = dict(RESOURCES)
        self.backup_resources = {}
        self.backup_hash = None
        self.operations = []
        self.corrupt_backup = False
        self.fail_candidate = False
        self.fail_transport = False

    def check_environment(self, **_kwargs):
        pass

    def shell(self, script, **_kwargs):
        self.operations.append(script)
        if script.startswith("pm path "):
            return "package:" + BASE if self.current else ""
        if script.startswith("dumpsys package "):
            return "versionName=15.535.13\nUser 0: installed=true stopped=true\n"
        if script.startswith("mkdir -m 700 "):
            self.backup_hash = self.current
        if script.startswith("if [ -d "):
            return "present" if script.startswith(f"if [ -d {installer.DATA} ];") else ""
        if script.startswith(f"cp -a {installer.DATA} "):
            self.backup_resources = copy.deepcopy(self.resources)
        if script.startswith("pm uninstall "):
            self.current, self.resources = None, {}
            return "Success"
        if script.startswith("stat -c %u "):
            return "10190"
        if script.startswith("mkdir -p ") and "/ce/. " in script:
            self.resources = copy.deepcopy(self.backup_resources)
        return ""

    def run(self, *args, check=True):
        if args[0] == "push":
            self.operations.append("push")
            return ""
        script = args[1]
        self.operations.append(script)
        if "previous.apk" in script:
            self.current = self.backup_hash
            return "Success"
        if " -r " in script:
            if self.fail_transport:
                self.current, self.resources = NEW, {}
                raise ConnectionError("disconnected after installation")
            return "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]"
        if self.fail_candidate:
            return "Failure [INSTALL_FAILED_INVALID_APK]"
        self.current = NEW
        return "Success"

    def hash(self, _device, path):
        if path.endswith("libg.so"):
            return installer.MANIFEST["libg_sha256"]
        if path.endswith("previous.apk"):
            return "0" * 64 if self.corrupt_backup else self.backup_hash
        if path.endswith("candidate.apk"):
            return NEW
        return self.current

    def hashes(self, _device, directory=installer.DATA + "/update"):
        return dict(self.backup_resources if "/ce/update" in directory else self.resources)


class InstallationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.receipt = Path(self.temp.name) / "install.json"
        self.device = FakeDevice()
        self.addCleanup(self.temp.cleanup)
        for name, replacement in (("validate_apk", lambda _: {"apkSha256": NEW}),
                                  ("file_hash", self.device.hash), ("resource_hashes", self.device.hashes)):
            context = patch.object(installer, name, replacement)
            context.start()
            self.addCleanup(context.stop)

    def install(self):
        return installer.install(self.device, Path("candidate.apk"), self.receipt)

    def test_signature_replacement_preserves_all_update_hashes_and_backup(self):
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(self.device.current, NEW)
        self.assertEqual(self.device.resources, RESOURCES)
        self.assertEqual(self.device.backup_hash, OLD)
        self.assertTrue(self.receipt.exists())

    def test_corrupt_backup_blocks_any_install_or_uninstall(self):
        self.device.corrupt_backup = True
        with self.assertRaisesRegex(ValueError, "备份 APK"):
            self.install()
        self.assertEqual(self.device.current, OLD)
        self.assertFalse(any(op.startswith("pm install") or op.startswith("pm uninstall") for op in self.device.operations))

    def test_failed_replacement_restores_previous_apk_and_resources(self):
        self.device.fail_candidate = True
        with self.assertRaisesRegex(RuntimeError, "已恢复"):
            self.install()
        self.assertEqual(self.device.current, OLD)
        self.assertEqual(self.device.resources, RESOURCES)

    def test_unknown_install_result_after_transport_failure_also_rolls_back(self):
        self.device.fail_transport = True
        with self.assertRaisesRegex(RuntimeError, "已恢复"):
            self.install()
        self.assertEqual(self.device.current, OLD)
        self.assertEqual(self.device.resources, RESOURCES)

    def test_missing_update_stops_before_replacing_game(self):
        self.device.resources = {}
        with self.assertRaisesRegex(ValueError, "原版游戏"):
            self.install()
        self.assertEqual(self.device.operations, [f"pm path {installer.PACKAGE}", f"pm path {installer.PACKAGE}", f"dumpsys package {installer.PACKAGE}"])

    def test_matching_apk_is_reused_without_extra_backup(self):
        self.device.current = NEW
        self.assertEqual(self.install()["status"], "already-installed")
        self.assertIsNone(self.device.backup_hash)

    def test_mumu_streaming_receipt_requires_a_final_success(self):
        installer.require_success("Success: streamed 794497101 bytes\r\n\r\nSuccess")
        with self.assertRaises(RuntimeError):
            installer.require_success("Success: streamed 794497101 bytes\nFailure [INSTALL_FAILED_INVALID_APK]")


class BootstrapPatchTest(unittest.TestCase):
    def test_update_bootstrap_has_no_probe_or_offline_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            game = source / "smali/com/supercell/clashroyale/GameApp.smali"
            titan = source / "smali/com/supercell/titan/GameApp.smali"
            game.parent.mkdir(parents=True)
            titan.parent.mkdir(parents=True)
            game.write_text('.method public final a()Z\n    .locals 3\n    return v0\n.end method\n', encoding="utf-8")
            titan.write_text('# direct methods\n    const-string v3, "g"\n    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n', encoding="utf-8")
            patch_bootstrap(source, update_only=True)
            self.assertNotIn("crprobe", game.read_text(encoding="utf-8") + titan.read_text(encoding="utf-8"))
            self.assertNotIn("nativeBootstrapProbe", titan.read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                patch_bootstrap(source, update_only=True)

    def test_library_notification_follows_libg_and_version_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            game = source / "smali/com/supercell/clashroyale/GameApp.smali"
            titan = source / "smali/com/supercell/titan/GameApp.smali"
            game.parent.mkdir(parents=True)
            titan.parent.mkdir(parents=True)
            original = '.method public final a()Z\n    .locals 3\n    return v0\n.end method\n'
            game.write_text(original, encoding="utf-8")
            titan.write_text("# direct methods\nunsupported\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_bootstrap(source)


class ContentValidationTest(unittest.TestCase):
    def test_known_fingerprint_variant_requires_matching_apk_fallbacks(self):
        files = dict(RESOURCES)
        files["fingerprint.json"] = installer.CONTENT_LOCK["fingerprints"][1]
        base = {name: files.pop(name) for name in installer.CONTENT_LOCK["apkFallbackFiles"]}
        installer.validate_resources(files, base)
        with self.assertRaises(ValueError):
            installer.validate_resources(files)
        base[next(iter(base))] = "0" * 64
        with self.assertRaises(ValueError):
            installer.validate_resources(files, base)

    def test_known_fingerprint_does_not_allow_modified_logic(self):
        files = dict(RESOURCES)
        files["csv_logic/globals.csv"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "globals.csv"):
            installer.validate_resources(files)
        files = dict(RESOURCES)
        files["fingerprint.json"] = "0" * 64
        with self.assertRaises(ValueError):
            installer.validate_resources(files)
            self.assertEqual(game.read_text(encoding="utf-8"), original)
            titan.write_text('# direct methods\n    const-string v3, "g"\n    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n', encoding="utf-8")
            patch_bootstrap(source)
            text = titan.read_text(encoding="utf-8")
            self.assertLess(text.index('"scid_sdk"'), text.index('"g"'))
            self.assertGreater(text.index("invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V"), text.index('"g"'))
            with self.assertRaises(ValueError):
                patch_bootstrap(source)


if __name__ == "__main__":
    unittest.main()
