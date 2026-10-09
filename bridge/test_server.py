import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.controller import BattleController
from bridge.server import BridgeServer
from bridge.test_controller import FakeEngine


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = BridgeServer(0, "a" * 64, BattleController())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_token_required_and_loopback_status_available(self):
        with self.assertRaises(HTTPError) as rejected:
            urlopen(self.base + "/v1/status", timeout=2)
        self.assertEqual(rejected.exception.code, 401)
        request = Request(self.base + "/v1/status", headers={"Authorization": "Bearer " + "a" * 64})
        with urlopen(request, timeout=2) as response:
            status = json.load(response)
        self.assertFalse(status["ready"])
        self.assertEqual(status["identity"]["upstreamCommit"],
                         "f92ccbecd6a1c9ad1fa3abaca54a51110607b6df")

    def test_unknown_browser_origin_has_no_cors_access(self):
        request = Request(self.base + "/v1/status", method="OPTIONS",
                          headers={"Origin": "https://example.com"})
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=2)
        self.assertEqual(rejected.exception.code, 403)

    def test_windows_desktop_origin_can_preflight(self):
        request = Request(self.base + "/v1/status", method="OPTIONS",
                          headers={"Origin": "http://tauri.localhost"})
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://tauri.localhost")

    def test_replay_route_accepts_snapshot_base_fields(self):
        engine = FakeEngine()
        self.server.controller.engine = engine
        self.server.controller.reset(41)
        base = self.server.controller.step(5)
        snapshot = self.server.controller.snapshot_create()
        request = Request(self.base + "/v1/replay", method="POST",
                          headers={"Authorization": "Bearer " + "a" * 64,
                                   "Content-Type": "application/json"},
                          data=json.dumps({"seed": 41, "actions": [], "targetTick": 100,
                                           "snapshotHandle": snapshot["handle"], "baseTick": 95,
                                           "baseDigest": base["digest"]}).encode())
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        self.assertEqual(result["tick"], 100)
        self.assertEqual(engine.reset_count, 1)


if __name__ == "__main__":
    unittest.main()
