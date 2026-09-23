import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.controller import BattleController
from bridge.server import BridgeServer


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


if __name__ == "__main__":
    unittest.main()
