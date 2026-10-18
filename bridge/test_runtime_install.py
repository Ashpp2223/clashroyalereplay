import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_already_published_identical_downloads_do_not_restart_the_game(self):
        for name in self.contents:
            self.hashes[name.removesuffix('.update')] = self.hashes[name]
        with patch.object(installer, 'save_receipt') as receipt:
            result = installer.recover_pending_downloads(self.device, self.hashes)
        self.assertEqual(result, self.hashes)
        self.assertEqual(self.operations, [])
        receipt.assert_not_called()


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
        self.fail_inplace = False
        self.free_kib = 20*1024**2

    def check_environment(self, **_kwargs):
        pass

    def shell(self, script, **_kwargs):
        self.operations.append(script)
        if script=='df -k /data':
            return f'Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/vda 24000000 1000000 {self.free_kib} 5% /data'
        if 'du -sk' in script:
            return '1024 '+installer.DATA if script.startswith(f'if [ -d {installer.DATA} ];') else ''
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
            if self.fail_inplace:
                return 'Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]'
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
        self.apk = Path(self.temp.name)/'candidate.apk'
        self.apk.write_bytes(b'candidate fixture')
        self.device = FakeDevice()
        self.addCleanup(self.temp.cleanup)
        for name, replacement in (("validate_apk", lambda _: {"apkSha256": NEW}),
                                  ("file_hash", self.device.hash), ("resource_hashes", self.device.hashes)):
            context = patch.object(installer, name, replacement)
            context.start()
            self.addCleanup(context.stop)

    def install(self):
        return installer.install(self.device, self.apk, self.receipt)

    def test_low_storage_stops_before_backup_or_install(self):
        self.device.free_kib=100
        with self.assertRaisesRegex(ValueError,'内部空间不足'):
            self.install()
        self.assertEqual(self.device.current,OLD)
        self.assertIsNone(self.device.backup_hash)
        self.assertFalse(any(op.startswith('pm install') or op.startswith('pm uninstall') for op in self.device.operations))

    def test_rejected_inplace_upgrade_never_uninstalls_intact_game(self):
        self.device.fail_inplace=True
        with self.assertRaisesRegex(RuntimeError,'原安装和资源保持完整'):
            self.install()
        self.assertEqual(self.device.current,OLD)
        self.assertEqual(self.device.resources,RESOURCES)
        self.assertFalse(any(op.startswith('pm uninstall') for op in self.device.operations))
        self.assertEqual(json.loads(self.receipt.read_text())['status'],'unchanged')

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
    def test_verified_local_apk_fallbacks_keep_full_resource_hash_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / 'prepared.apk'
            contents = {name: ('fixture:' + name).encode() for name in installer.CONTENT_LOCK['apkFallbackFiles']}
            with zipfile.ZipFile(apk, 'w') as archive:
                for name, data in contents.items():
                    archive.writestr('assets/' + name, data)
            hashes = dict(RESOURCES)
            for name in contents:
                del hashes[name]
            device = Mock()
            actual = installer.base_asset_hashes(device, hashes, apk)
            self.assertEqual(actual, {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()})
            device.shell.assert_not_called()
            # A local file is an alternate source of bytes, not permission to
            # trust missing or changed contents merely because an APK was checked.
            with self.assertRaisesRegex(ValueError, '缺失或不匹配'):
                installer.validate_resources(hashes, actual)
            supplied = next(iter(contents))
            hashes[supplied] = 'already-verified'
            self.assertNotIn(supplied, installer.base_asset_hashes(device, hashes, apk))

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


class StartupResourcesTest(unittest.TestCase):
    def test_startup_selection_checks_all_required_files_and_pending_conflicts(self):
        device = Mock()
        device.shell.side_effect = [
            OLD + "  ./fingerprint.json\n" + NEW + "  ./sc/optional.sc.update",
            "f" * 64 + "  /data/user/0/game/update/sc/optional.sc",
        ]
        hashes = installer.resource_hashes(device, "/data/user/0/game/update", required_only=True)
        script = device.shell.call_args_list[0].args[0]
        for name in installer.CONTENT_LOCK["requiredFiles"]:
            self.assertIn("./" + name, script)
        self.assertIn("-name '*.update'", script)
        self.assertEqual(hashes['sc/optional.sc'], 'f' * 64)
        self.assertEqual(hashes['sc/optional.sc.update'], NEW)
        with patch.dict(installer.CONTENT_LOCK, {'fingerprints': [OLD]}), \
             patch.object(installer, 'file_hash', return_value=OLD):
            device.shell.side_effect = ['', json.dumps({'files':[{'file':'sc/optional.sc','sha':'a'*40}]}), 'a'*40 + ' file']
            with self.assertRaisesRegex(ValueError, '冲突'):
                installer.recover_pending_downloads(device, hashes)
        self.assertFalse(any('cp ' in call.args[0] for call in device.shell.call_args_list))

    def test_full_backup_inventory_still_hashes_every_file(self):
        device = Mock()
        device.shell.return_value = OLD + '  ./optional-file'
        self.assertEqual(installer.resource_hashes(device), {'optional-file': OLD})
        script = device.shell.call_args.args[0]
        self.assertIn('find . -type f -exec sha256sum', script)
        self.assertNotIn('-path', script)

    def test_startup_rejects_missing_or_changed_required_content(self):
        for value in (None, NEW):
            hashes = dict(RESOURCES)
            name = next(iter(installer.CONTENT_LOCK['requiredFiles']))
            if value is None:
                del hashes[name]
            else:
                hashes[name] = value
            with self.assertRaisesRegex(ValueError, '缺失或不匹配'):
                installer.validate_resources(hashes)


class RuntimeVerificationTest(unittest.TestCase):
    def setUp(self):
        from bridge.runtime import AndroidRuntime, MANIFEST, PROBE_SHA256
        self.runtime = AndroidRuntime.__new__(AndroidRuntime)
        self.runtime.apk_sha256 = NEW
        self.calls = []
        self.apk_started = threading.Event()
        self.resources_started = threading.Event()
        self.apk_hashes = [NEW]

        def command(*args, **kwargs):
            self.calls.append(args)
            if args[1:3] == ('getprop', 'ro.product.cpu.abilist'):
                return SimpleNamespace(stdout='x86_64,arm64-v8a')
            if args[1:3] == ('pm', 'path'):
                return SimpleNamespace(stdout='package:' + BASE)
            if args[-1] == BASE:
                self.apk_started.set()
                if not self.resources_started.wait(2):
                    raise AssertionError('Resource verification did not overlap APK hashing')
                return SimpleNamespace(stdout=self.apk_hashes.pop(0) + '  ' + BASE)
            return SimpleNamespace(stdout=(MANIFEST['libg_sha256'] if args[-1].endswith('libg.so') else PROBE_SHA256) + '  file')

        def resources():
            self.resources_started.set()
            self.assertTrue(self.apk_started.wait(2))
        self.runtime.command = command
        self.runtime._check_updated_content = resources
        self.runtime._install_local_prepared = Mock()

    def test_startup_overlaps_checks_and_still_verifies_all_binaries(self):
        self.runtime._check_installed_runtime()
        self.runtime._install_local_prepared.assert_not_called()
        for name in ('libg.so', 'libcrprobe.so', 'base.apk'):
            self.assertTrue(any(call[-1].endswith(name) for call in self.calls))

    def test_changed_apk_requires_install_and_new_binary_checks(self):
        self.apk_hashes = [OLD, NEW]
        self.runtime._check_installed_runtime()
        self.runtime._install_local_prepared.assert_called_once()
        self.assertEqual(sum(call[-1] == BASE for call in self.calls), 2)
        self.assertEqual(sum(call[-1].endswith('libg.so') for call in self.calls), 2)

    def test_resource_failure_cannot_trigger_install(self):
        def invalid_resources():
            self.resources_started.set()
            raise ValueError('资源缺失')
        self.runtime._check_updated_content = invalid_resources
        with self.assertRaisesRegex(ValueError, '资源缺失'):
            self.runtime._check_installed_runtime()
        self.runtime._install_local_prepared.assert_not_called()

    def test_unchanged_installed_apk_and_libraries_reuse_scan_but_check_resources(self):
        stamp = {'serial': 'emulator-5560', 'path': BASE, 'stat': 'unchanged'}
        self.runtime._apk_cache = {'installed': stamp}
        self.runtime._installed_apk_stamp = Mock(return_value=stamp)
        self.runtime._check_updated_content = Mock()
        self.runtime._check_installed_runtime()
        self.assertFalse(self.apk_started.is_set())
        self.assertTrue(self.runtime.apk_cache_hit)
        self.runtime._check_updated_content.assert_called_once()
        self.assertFalse(any('sha256sum' in call for call in self.calls))

    def test_changed_apk_metadata_requires_the_full_hash_again(self):
        self.runtime._apk_cache = {'installed': {'stat': 'old'}}
        stamp = {'serial': 'emulator-5560', 'path': BASE, 'stat': 'new'}
        self.runtime._installed_apk_stamp = Mock(return_value=stamp)
        self.runtime._check_installed_runtime()
        self.assertTrue(self.apk_started.is_set())
        self.assertFalse(self.runtime.apk_cache_hit)
        self.assertEqual(self.runtime._apk_cache['installed'], stamp)

    def test_apk_changing_during_verification_cannot_be_cached(self):
        self.runtime._apk_cache = {}
        self.runtime._installed_apk_stamp = Mock(side_effect=[{'stat': 'before'}, {'stat': 'after'}])
        with self.assertRaisesRegex(RuntimeError, '校验期间发生变化'):
            self.runtime._check_installed_runtime()
        self.assertNotIn('installed', self.runtime._apk_cache)

    def test_verification_cache_is_bound_to_version_and_device_configuration(self):
        from bridge import runtime
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'RUNTIME_HOME', Path(directory)):
            android = runtime.AndroidRuntime()
            android._load_apk_cache()
            android._apk_cache['installed'] = {'stat': 'verified'}
            android._save_apk_cache()
            for field, value in (('apk_sha256', 'f' * 64), ('preferred_serial', 'emulator-5558'),
                                 ('config', {'avdHome': 'another device'})):
                other = runtime.AndroidRuntime()
                other._load_apk_cache()
                self.assertIn('installed', other._apk_cache)
                setattr(other, field, value)
                other._load_apk_cache()
                self.assertNotIn('installed', other._apk_cache)
            (Path(directory) / 'apk-verification.json').write_text('incomplete')
            android._load_apk_cache()
            self.assertNotIn('installed', android._apk_cache)

    def test_install_stamp_covers_apk_and_both_libraries_and_fails_closed(self):
        from bridge.runtime import AndroidRuntime
        android = AndroidRuntime.__new__(AndroidRuntime)
        android._apk_cache = {};android.config = {'backend': 'android-sdk'};android.serial = 'emulator-5560'
        line = '65029:270351:794673452:2026-10-10 00:31:13.152000000 +0000:2026-10-10 00:31:19.344000000 +0000'
        android.command = Mock(return_value=SimpleNamespace(stdout='\n'.join([line] * 3), returncode=0))
        self.assertIsNotNone(android._installed_apk_stamp(BASE))
        self.assertEqual(android.command.call_args.args[-3:], (BASE, BASE.rsplit('/', 1)[0] + '/lib/arm64/libg.so',
                                                              BASE.rsplit('/', 1)[0] + '/lib/arm64/libcrprobe.so'))
        for output, code in ((line, 0), ('\n'.join([line] * 3), 1), ('malformed\n' * 3, 0)):
            android.command.return_value = SimpleNamespace(stdout=output, returncode=code)
            self.assertIsNone(android._installed_apk_stamp(BASE))


if __name__ == "__main__":
    unittest.main()
