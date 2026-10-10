from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from bridge.setup import SetupManager, extract_checked, write_avd, download_checked


class SetupTest(unittest.TestCase):
    def test_archive_cannot_escape_user_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as file:
                file.writestr("../escape.txt", "not allowed")
            with self.assertRaisesRegex(ValueError, "路径"):
                extract_checked(archive, Path(folder) / "runtime")
            self.assertFalse((Path(folder) / "escape.txt").exists())

    def test_wrong_official_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as file:
                file.writestr("other/emulator.exe", "wrong")
            with self.assertRaisesRegex(ValueError, "目录"):
                extract_checked(archive, Path(folder) / "sdk", "emulator")

    def test_license_acceptance_is_required(self):
        with self.assertRaisesRegex(ValueError, "条款"):
            SetupManager().start(False)

    def test_resources_require_a_configured_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "先安装"):
                SetupManager(folder).import_resources("missing.zip", None)

    def test_existing_environment_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime.json").write_text("user environment")
            manager = SetupManager(root)
            manager._install()
            self.assertEqual(manager.status["state"], "failed")
            self.assertEqual((root / "runtime.json").read_text(), "user environment")

    def test_new_device_references_the_installed_sdk_and_own_disk(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_avd(root, root / "sdk")
            settings = json.loads((root / "runtime.json").read_text())
            self.assertEqual(settings["avdName"], "CR_Replay_API31")
            self.assertIn("disk.dataPartition.size=20G",(root/"avd/CR_Replay_API31.avd/config.ini").read_text())
            self.assertTrue((Path(settings["avdHome"]) / "CR_Replay_API31.ini").is_file())
            self.assertIn(str(root / "sdk").replace("\\", "/"), (root / "avd/CR_Replay_API31.avd/config.ini").read_text())
