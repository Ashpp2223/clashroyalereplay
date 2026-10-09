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


if __name__ == "__main__":
    unittest.main()
