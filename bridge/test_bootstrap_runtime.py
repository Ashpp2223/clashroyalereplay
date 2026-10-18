from types import SimpleNamespace
from pathlib import Path
import subprocess
import tempfile
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

    def test_snapshot_shutdown_keeps_game_isolated_and_only_removes_its_forward(self):
        with patch.object(bootstrap, 'cleanup_command', return_value=SimpleNamespace(
                returncode=0, stdout='emulator-5560 tcp:26790 tcp:26789\n')) as command, \
                patch.object(bootstrap, 'clear_offline_rules') as clear:
            bootstrap.shutdown('adb', 'emulator-5560', 26790, preserve_game=True)
        clear.assert_not_called()
        self.assertEqual([call.args[2:] for call in command.call_args_list],
                         [('forward', '--list'), ('forward', '--remove', 'tcp:26790')])


class BootstrapVerificationTest(unittest.TestCase):
    def test_saved_ready_game_resumes_without_starting_a_new_process(self):
        upstream = SimpleNamespace(PACKAGE='game', ACTIVITY='game/activity',
                                   command=Mock(return_value=SimpleNamespace(stdout='')),
                                   shell=Mock(return_value=SimpleNamespace(stdout='10001')),
                                   read_status=Mock(return_value={'contentReady': True, 'nativeRenderReady': True, 'paused': True}))
        with patch.object(bootstrap, 'upstream_bootstrap', return_value=upstream), \
                patch.object(bootstrap, 'clear_offline_rules'), patch.object(bootstrap, 'wait_until_ready') as wait:
            self.assertTrue(bootstrap.bootstrap('adb', 'emulator-5560', verified=True, resume=True)['resumed'])
        self.assertFalse(any('force-stop' in call.args or 'start' in call.args for call in upstream.shell.call_args_list))
        wait.assert_not_called()
        self.assertTrue(any('iptables' in call.args for call in upstream.shell.call_args_list))

    def test_unavailable_or_running_saved_game_falls_back_to_fresh_start(self):
        for status in (TimeoutError('not responding'), {'contentReady': False},
                       {'contentReady': True, 'nativeRenderReady': True, 'paused': False}):
            upstream = SimpleNamespace(PACKAGE='game', ACTIVITY='game/activity',
                                       command=Mock(return_value=SimpleNamespace(stdout='')),
                                       shell=Mock(return_value=SimpleNamespace(stdout='10001')), read_status=Mock())
            if isinstance(status, Exception):
                upstream.read_status.side_effect = status
            else:
                upstream.read_status.return_value = status
            with patch.object(bootstrap, 'upstream_bootstrap', return_value=upstream), \
                    patch.object(bootstrap, 'clear_offline_rules'), \
                    patch.object(bootstrap, 'wait_until_ready', return_value={'contentReady': True}) as wait:
                self.assertNotIn('resumed', bootstrap.bootstrap('adb', 'emulator-5560', verified=True, resume=True))
            wait.assert_called_once()
            self.assertTrue(any('force-stop' in call.args for call in upstream.shell.call_args_list))

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
    def test_cached_installation_still_requires_matching_live_attestation(self):
        from bridge import runtime
        android = runtime.AndroidRuntime()
        android.apk_cache_hit = True
        claim = {'package_name': runtime.MANIFEST['package'], 'abi': runtime.MANIFEST['abi'],
                 'probe_sha256': runtime.PROBE_SHA256, 'libg_sha256': runtime.MANIFEST['libg_sha256'],
                 'content_fingerprint_sha256': android.runtime_fingerprint_sha256,
                 'content_assets_sha256': runtime.CONTENT_LOCK['requiredFiles']['assets.scdb'],
                 'content_version': runtime.MANIFEST['runtime_content_version'], 'production_ready': True,
                 'capabilities': {name: True for name in ('native_render', 'snapshot_restore', 'structured_observation', 'two_sided_actions')}}
        for field in (None, 'probe_sha256', 'libg_sha256', 'content_assets_sha256'):
            response = dict(claim)
            if field:
                response[field] = '0' * 64
            engine = Mock();engine.request.return_value = {'ok': True, 'attestation': response}
            with patch('bridge.engine_adapter.new_engine', return_value=engine):
                if field:
                    with self.assertRaisesRegex(RuntimeError, field):
                        android._attest()
                else:
                    self.assertEqual(android._attest(), claim)
            engine.request.assert_called_once_with('attest')
            engine.close.assert_called_once()

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
                android._verified_apk = apk
                self.assertEqual(android.apk_sha256, expected)
                digest.assert_not_called()
                android.adb = apk
                with patch.object(android, '_find_arm64_device') as discover:
                    with self.assertRaisesRegex(RuntimeError, 'APK 缺失或哈希不符'):
                        android.start()
                    discover.assert_not_called()
                self.assertIsNone(android._verified_apk)
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


class EmulatorLifecycleTest(unittest.TestCase):
    def test_adb_restart_or_abi_timeout_can_be_retried_during_boot(self):
        from bridge import runtime
        android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
        android.adb = 'adb';android.preferred_serial = 'emulator-5560'
        listing = SimpleNamespace(returncode=0, stdout='List of devices attached\nemulator-5556 device\nemulator-5560 device\n')
        responses = [subprocess.TimeoutExpired('adb devices', 15), SimpleNamespace(returncode=1, stdout=''),
                     listing, subprocess.TimeoutExpired('getprop', 8), listing,
                     SimpleNamespace(returncode=0, stdout='x86_64,arm64-v8a')]
        with patch.object(runtime.subprocess, 'run', side_effect=responses) as command:
            self.assertIsNone(android._find_arm64_device())
            self.assertIsNone(android._find_arm64_device())
            self.assertIsNone(android._find_arm64_device())
            self.assertEqual(android._find_arm64_device(), 'emulator-5560')
        self.assertFalse(any('emulator-5556' in call.args[0] for call in command.call_args_list))

    def test_snapshot_shutdown_pauses_game_and_retains_offline_rules_only_for_owned_emulator(self):
        from bridge import runtime
        for owned, pause_ok in ((True, True), (True, False), (False, True)):
            android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
            android.adb = 'adb';android.serial = 'emulator-5560';android.video = None
            android.owns_session = True;android._ready_for_snapshot = True
            android.process = Mock() if owned else None
            if owned:
                android.process.poll.return_value = None
            android._stop_owned_emulator = Mock()
            engine = Mock()
            engine.request.return_value = {'ok': pause_ok}
            with patch('bridge.engine_adapter.new_engine', return_value=engine), patch.object(bootstrap, 'shutdown') as shutdown:
                android.close()
            self.assertEqual(shutdown.call_args.kwargs['preserve_game'], owned and pause_ok)
            self.assertFalse(android._ready_for_snapshot)
            android._stop_owned_emulator.assert_called_once()
            if owned:
                engine.request.assert_any_call('pause')
                engine.request.assert_any_call('replay-schedule-clear')

    def stalled_runtime(self, *, owned=True, quick=True):
        from bridge.runtime import AndroidRuntime
        android = AndroidRuntime.__new__(AndroidRuntime)
        android.process = Mock(pid=1234, returncode=None) if owned else None
        if android.process:
            android.process.poll.return_value = None
        android._using_quick_boot = quick
        android._connect_selected_device = Mock()
        android._stop_owned_emulator = Mock()
        android._find_arm64_device = Mock(return_value=None)
        android._launch_sdk_emulator = Mock()
        return android

    def test_stalled_owned_quick_boot_retries_without_snapshot_once(self):
        from bridge import runtime
        android = self.stalled_runtime()
        clock = [0]
        cold = [False]
        android._find_arm64_device.side_effect = lambda: 'emulator-5560' if cold[0] else None
        android._launch_sdk_emulator.side_effect = lambda **kwargs: cold.__setitem__(0, kwargs['cold_boot'])
        with patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(runtime.time, 'sleep', side_effect=lambda _: clock.__setitem__(0, clock[0] + 15)):
            self.assertEqual(android._wait_for_avd(120), 'emulator-5560')
        android._stop_owned_emulator.assert_called_once_with(timeout=5)
        android._launch_sdk_emulator.assert_called_once_with(cold_boot=True)
        self.assertLess(clock[0], 120)

    def test_external_devices_and_first_cold_boot_are_never_restarted(self):
        from bridge import runtime
        for owned, quick in ((False, True), (True, False)):
            with self.subTest(owned=owned, quick=quick):
                android = self.stalled_runtime(owned=owned, quick=quick)
                clock = [0]
                with patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]), \
                        patch.object(runtime.time, 'sleep', side_effect=lambda _: clock.__setitem__(0, clock[0] + 15)):
                    with self.assertRaisesRegex(TimeoutError, '模拟器启动超时'):
                        android._wait_for_avd(60)
                android._stop_owned_emulator.assert_not_called()
                android._launch_sdk_emulator.assert_not_called()

    def test_failed_cold_retry_keeps_the_total_timeout_and_does_not_loop(self):
        from bridge import runtime
        android = self.stalled_runtime()
        clock = [0]
        with patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(runtime.time, 'sleep', side_effect=lambda _: clock.__setitem__(0, clock[0] + 15)):
            with self.assertRaises(TimeoutError):
                android._wait_for_avd(120)
        self.assertEqual(clock[0], 120)
        android._launch_sdk_emulator.assert_called_once_with(cold_boot=True)

    def test_shutdown_addresses_owned_emulator_even_before_adb_is_online(self):
        from bridge import runtime
        android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
        process = android.process = Mock(pid=1234)
        process.poll.return_value = None
        android.adb = 'adb'
        android.serial = None
        android.preferred_serial = 'emulator-5560'
        with patch.object(runtime.subprocess, 'run') as command:
            android._stop_owned_emulator()
        self.assertEqual(command.call_args.args[0], ['adb', '-s', 'emulator-5560', 'emu', 'kill'])
        process.wait.assert_called_once_with(timeout=45)
        self.assertIsNone(android.process)

    def test_timed_out_shutdown_kills_only_the_owned_process_tree(self):
        from bridge import runtime
        android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
        process = android.process = Mock(pid=1234)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('emulator', 5), 0]
        android.adb = 'adb';android.serial = 'emulator-5560'
        with patch.object(runtime.os, 'name', 'nt'), patch.object(runtime.subprocess, 'run') as command:
            android._stop_owned_emulator(timeout=5)
        self.assertEqual(command.call_args.args[0], ['taskkill', '/PID', '1234', '/T', '/F'])
        self.assertIsNone(android.process)

    def test_cold_retry_skips_snapshot_load_without_wiping_avd_data(self):
        from bridge import runtime
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            emulator = home / 'emulator.exe';emulator.touch()
            (home / 'Test.ini').touch()
            snapshot = home / 'Test.avd/snapshots/default_boot/snapshot.pb'
            snapshot.parent.mkdir(parents=True);snapshot.touch()
            for cold in (False, True):
                android = runtime.AndroidRuntime.__new__(runtime.AndroidRuntime)
                android.preferred_serial = 'emulator-5560';android.emulator = emulator
                android.config = {'avdHome': str(home)};android.avd_name = 'Test'
                with patch.object(runtime, 'RUNTIME_HOME', home), patch.object(runtime.subprocess, 'Popen') as start:
                    android._launch_sdk_emulator(cold_boot=cold)
                args = start.call_args.args[0]
                self.assertEqual('-no-snapshot-load' in args, cold)
                self.assertEqual(android._using_quick_boot, not cold)
                self.assertNotIn('-wipe-data', args)


if __name__ == "__main__":
    unittest.main()
