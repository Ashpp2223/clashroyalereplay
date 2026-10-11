from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge import bootstrap_runtime as bootstrap


class CleanupTest(unittest.TestCase):
    def test_dead_game_aborts_before_readiness_timeout(self):
        upstream = SimpleNamespace(PACKAGE="game", read_status=lambda port: {"contentReady": False},
                                   shell=lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
        with patch.object(bootstrap.time, "monotonic", side_effect=[0, 6, 6]), \
                patch.object(bootstrap, "cleanup_command", return_value=None), \
                patch.object(bootstrap.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "游戏进程已退出"):
                bootstrap.wait_until_ready(upstream, "adb", "emulator-5556", 26789, 90)
        sleep.assert_not_called()

    def test_live_game_can_finish_content_loading(self):
        upstream = SimpleNamespace(PACKAGE="game")
        upstream.read_status = Mock(side_effect=[{"contentReady": False}, {"contentReady": True}])
        upstream.shell = Mock(return_value=SimpleNamespace(returncode=0, stdout="1234"))
        with patch.object(bootstrap.time, "monotonic", side_effect=[0, 6, 6, 7]), \
                patch.object(bootstrap.time, "sleep"):
            self.assertTrue(bootstrap.wait_until_ready(upstream, "adb", "emulator-5556", 26789, 90)["contentReady"])

    def test_removes_only_our_rules_in_reverse_order(self):
        calls = []

        def command(_adb, _serial, *args):
            calls.append(args)
            listing = "1 REJECT /* other-app */\n3 REJECT /* cr-replay-offline */\n5 REJECT /* cr-replay-offline */\n"
            return SimpleNamespace(returncode=0, stdout=listing)

        with patch.object(bootstrap, "cleanup_command", command):
            bootstrap.clear_offline_rules(None, "adb", "127.0.0.1:16416")
        self.assertEqual([args[-1] for args in calls if "-D" in args], [5, 3, 5, 3])

    def test_shutdown_preserves_another_devices_forward(self):
        calls = []

        def command(_adb, _serial, *args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="other-device tcp:26789 tcp:26789\n")

        with patch.object(bootstrap, "cleanup_command", command):
            bootstrap.shutdown("adb", "127.0.0.1:16416")
        self.assertFalse(any("--remove" in args for args in calls))

    def test_shutdown_removes_its_own_forward(self):
        calls = []

        def command(_adb, _serial, *args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="127.0.0.1:16416 tcp:26789 tcp:26789\n")

        with patch.object(bootstrap, "cleanup_command", command):
            bootstrap.shutdown("adb", "127.0.0.1:16416")
        self.assertIn(("forward", "--remove", "tcp:26789"), calls)


class BootstrapVerificationTest(unittest.TestCase):
    def test_runtime_reuses_immediately_completed_checks_but_cli_verifies(self):
        for verified in (False, True):
            upstream = SimpleNamespace(PACKAGE='game', ACTIVITY='game/activity',
                                       command=Mock(return_value=SimpleNamespace(stdout='')),
                                       shell=Mock(side_effect=lambda *args, **kwargs: SimpleNamespace(stdout='10001' if 'stat' in args else 'arm64-v8a')),
                                       app_directory=Mock(return_value='/data/app/game'), verify_engine=Mock())
            with patch.object(bootstrap, 'upstream_bootstrap', return_value=upstream), \
                 patch.object(bootstrap, 'clear_offline_rules'), \
                 patch.object(bootstrap, 'wait_until_ready', return_value={'contentReady':True}):
                self.assertTrue(bootstrap.bootstrap('adb','emulator-5560',verified=verified)['contentReady'])
            self.assertEqual(upstream.verify_engine.call_count, 0 if verified else 1)
            self.assertTrue(any('force-stop' in call.args for call in upstream.shell.call_args_list))
            self.assertTrue(any('forward' in call.args for call in upstream.command.call_args_list))


class BridgeGreetingTest(unittest.TestCase):
    def test_local_apk_hash_runs_during_startup_and_tampering_blocks_android(self):
        import json
        import tempfile
        from pathlib import Path
        from bridge import runtime
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            apk = home / 'prepared.apk'
            apk.write_bytes(b'changed')
            expected = '1' * 64
            (home / 'prepared.json').write_text(json.dumps({'path':str(apk),'apkSha256':expected}))
            with patch.object(runtime, 'RUNTIME_HOME', home), patch.object(runtime, 'sha256', return_value='2'*64) as digest:
                android = runtime.AndroidRuntime()
                self.assertEqual(android.apk_sha256, expected)
                digest.assert_not_called()
                android.adb = apk
                with patch.object(android, '_find_arm64_device') as discover:
                    with self.assertRaisesRegex(RuntimeError, 'APK 缺失或哈希不符'):
                        android.start()
                    discover.assert_not_called()
                digest.assert_called_once_with(apk)


    def test_verified_install_hashes_once_but_replacement_is_rechecked(self):
        from bridge import runtime
        apk = '/data/app/game/base.apk'
        for installed, bad_probe in ((True, False), (False, False), (True, True)):
            android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
            android.apk_sha256 = '1'*64
            android._check_updated_content = Mock()
            android._install_local_prepared = Mock()
            hashes = []
            def command(*args, **kwargs):
                if args[1] == 'getprop':
                    output = 'arm64-v8a'
                elif args[1] == 'pm':
                    output = 'package:' + apk
                else:
                    path = args[-1]
                    hashes.append(path)
                    output = (runtime.MANIFEST['libg_sha256'] if path.endswith('/libg.so') else
                              ('f'*64 if bad_probe else runtime.PROBE_SHA256) if path.endswith('/libcrprobe.so') else
                              '0'*64 if not installed and hashes.count(apk)==1 else android.apk_sha256) + '  file'
                return SimpleNamespace(stdout=output)
            android.command = Mock(side_effect=command)
            if bad_probe:
                with self.assertRaisesRegex(RuntimeError, '探针 哈希不符'):
                    android._check_installed_runtime()
            else:
                android._check_installed_runtime()
            self.assertEqual(hashes.count(apk), 1 if installed else 2)
            self.assertEqual(sum(path.endswith('/libg.so') for path in hashes), 1 if installed else 2)
            self.assertEqual(sum(path.endswith('/libcrprobe.so') for path in hashes), 1)
            self.assertEqual(android._install_local_prepared.call_count, 0 if installed else 1)
            android._check_updated_content.assert_called_once()


if __name__ == "__main__":
    unittest.main()
