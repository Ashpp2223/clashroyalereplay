import json
import threading
import unittest
import time
import http.client
import tempfile
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch, Mock

from bridge.controller import BattleController
from bridge.server import BridgeServer
from bridge.test_controller import FakeEngine


class ServerTest(unittest.TestCase):
    def test_background_keepalive_reaches_paused_native_connection_without_desktop_polling(self):
        native=SimpleNamespace(connection=object(),status=Mock(return_value={'ok':True}))
        self.server.controller.engine=FakeEngine()
        self.server.controller.reset(41)
        self.server.controller.engine.native=native
        self.server._last_keepalive=time.monotonic()-6
        deadline=time.monotonic()+2
        while not native.status.called and time.monotonic()<deadline:time.sleep(.02)
        native.status.assert_called_once_with()
        self.assertFalse(self.server.controller.playing)

    def test_background_keepalive_failure_is_reported_and_persisted(self):
        from pathlib import Path
        self.server.controller.engine=FakeEngine()
        self.server.controller.reset(41)
        self.server.controller.engine.native=SimpleNamespace(connection=object(),status=Mock(side_effect=ConnectionAbortedError(10053,'Disconnected')))
        self.server._last_keepalive=time.monotonic()-6
        deadline=time.monotonic()+2
        failure_path=Path(self.runtime_home.name)/'last-failure.json'
        while (self.server.last_failure is None or not failure_path.exists()) and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(self.server.last_failure['path'],'/native/keepalive')
        self.assertIn('10053',self.server.controller.last_error)
        self.assertIsNone(self.server.controller.engine.state)
        self.assertTrue(failure_path.exists())

    def setUp(self):
        from pathlib import Path
        self.runtime_home = tempfile.TemporaryDirectory()
        self.runtime_patch = patch('bridge.server.RUNTIME_HOME', Path(self.runtime_home.name))
        self.runtime_patch.start()
        self.server = BridgeServer(0, "a" * 64, BattleController())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.runtime_patch.stop()
        self.runtime_home.cleanup()

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

    def test_status_remains_available_during_serial_battle_work(self):
        held = threading.Event()
        release = threading.Event()
        def hold():
            with self.server.controller.lock:
                held.set()
                release.wait(3)
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(held.wait(1))
        try:
            req = Request(self.base + "/v1/status", headers={"Authorization": "Bearer " + "a" * 64})
            with urlopen(req, timeout=1) as response:
                self.assertEqual(response.status, 200)
        finally:
            release.set()
            worker.join(2)

    def test_replay_route_returns_verified_ancestor_snapshots(self):
        self.server.controller.engine = FakeEngine()
        root = self.server.controller.reset(41)
        checkpoint = {'nodeId': 'root', 'tick': root['tick'], 'digest': root['digest']}
        request = Request(self.base + '/v1/replay', method='POST',
                          headers={'Authorization': 'Bearer ' + 'a' * 64, 'Content-Type': 'application/json'},
                          data=json.dumps({'seed': 41, 'actions': [], 'targetTick': 120,
                                           'checkpoints': [checkpoint]}).encode())
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        self.assertEqual(result['tick'], 120)
        self.assertEqual(result['snapshots'], [{**checkpoint, 'handle': 1}])
        self.assertEqual(self.server.controller.snapshot_restore(1)['digest'], root['digest'])

    def test_tower_selection_is_forwarded_on_reset_and_replay(self):
        engine=FakeEngine()
        self.server.controller.engine=engine
        config={'seed':41,'level':16,'tower0':159000002,'tower1':159000004}
        for path,extra in [('/v1/reset',{}),('/v1/replay',{'actions':[],'targetTick':100})]:
            request=Request(self.base+path,data=json.dumps({**config,**extra}).encode(),
                headers={'Authorization':'Bearer '+'a'*64,'Content-Type':'application/json'})
            with urlopen(request,timeout=2) as response:
                self.assertEqual(response.status,200)
            self.assertEqual(engine.configured_towers,(159000002,159000004))
        request=Request(self.base+'/v1/reset',data=json.dumps({**config,'tower1':159000003}).encode(),
            headers={'Authorization':'Bearer '+'a'*64,'Content-Type':'application/json'})
        resets=engine.reset_count
        with self.assertRaises(HTTPError) as rejected:urlopen(request,timeout=2)
        self.assertEqual(rejected.exception.code,400)
        self.assertEqual(engine.reset_count,resets)

    def test_frame_failure_pauses_the_battle(self):
        calls = []
        controller = self.server.controller
        controller.playing = True
        controller.engine = SimpleNamespace(pause=lambda: (calls.append("pause") or FakeEngine()._state(90, ())))
        def broken_frame():
            raise ConnectionError("decoder disconnected")
        controller.runtime = SimpleNamespace(frame=broken_frame, apk_sha256="test", runtime_fingerprint_sha256="test", stage="ready")
        req = Request(self.base + "/v1/frame", headers={"Authorization": "Bearer " + "a" * 64})
        with self.assertRaises(HTTPError):
            urlopen(req, timeout=2)
        self.assertEqual(calls, ["pause"])
        self.assertFalse(controller.playing)

    def test_paused_frame_request_bypasses_the_buffered_video(self):
        calls=[]
        self.server.controller.runtime=SimpleNamespace(frame=lambda fresh=False:(calls.append(fresh) or (b'current-frame' if fresh else b'buffered-frame','image/png')))
        for query,expected in [('',b'buffered-frame'),('?fresh=1',b'current-frame')]:
            req=Request(self.base+'/v1/frame'+query,headers={'Authorization':'Bearer '+'a'*64})
            with urlopen(req,timeout=2) as response:
                self.assertEqual(response.read(),expected)
        self.assertEqual(calls,[False,True])

    def test_frame_connection_is_reused_after_cors_preflight(self):
        self.server.controller.runtime = SimpleNamespace(frame=lambda: (b"frame", "image/jpeg"))
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.request("OPTIONS", "/v1/frame", headers={"Origin": "http://tauri.localhost"})
            response = connection.getresponse()
            self.assertEqual(response.status, 204)
            self.assertEqual(response.getheader("Access-Control-Max-Age"), "600")
            self.assertEqual(response.read(), b"")
            socket = connection.sock
            self.assertIsNotNone(socket)
            for _ in range(3):
                connection.request("GET", "/v1/frame", headers={"Authorization": "Bearer " + "a" * 64})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"frame")
                self.assertIs(connection.sock, socket)
        finally:
            connection.close()

    def test_cancelled_frame_response_does_not_suspend_the_game(self):
        controller = self.server.controller
        controller.playing = True
        controller.engine = SimpleNamespace(pause=lambda: self.fail("Cancelled response paused the game"))
        controller.runtime = SimpleNamespace(frame=lambda: (b"frame", "image/jpeg"))
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            with patch("bridge.server.BridgeRequest.end_headers", side_effect=ConnectionResetError):
                connection.request("GET", "/v1/frame", headers={"Authorization": "Bearer " + "a" * 64})
                with self.assertRaises(http.client.RemoteDisconnected):
                    connection.getresponse()
            self.assertTrue(controller.playing)
            self.assertIsNone(controller.last_error)
        finally:
            connection.close()

    def test_single_step_expected_tick_is_checked_before_advancing(self):
        self.server.controller.engine=FakeEngine()
        self.server.controller.reset(41)
        def request():
            return Request(self.base+'/v1/step',method='POST',headers={'Authorization':'Bearer '+'a'*64,'Content-Type':'application/json'},data=json.dumps({'ticks':1,'expectedTick':90}).encode())
        with urlopen(request(),timeout=2) as response:
            self.assertEqual(json.load(response)['tick'],91)
        with self.assertRaises(HTTPError) as stale:
            urlopen(request(),timeout=2)
        self.assertEqual(stale.exception.code,400)
        self.assertEqual(self.server.controller.engine.state.tick,91)

    def test_missing_contact_pauses_but_invalid_requests_do_not_renew_lease(self):
        calls = []
        controller = self.server.controller
        controller.playing = True
        controller.engine = SimpleNamespace(pause=lambda: (calls.append("pause") or FakeEngine()._state(90, ())))
        self.server.last_contact = time.monotonic() - 10
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/v1/status", timeout=2)
        deadline = time.monotonic() + 2
        while controller.playing and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(controller.playing)
        self.assertEqual(calls, ["pause"])

    def test_diagnostics_omit_token_and_private_home(self):
        from pathlib import Path
        self.server.controller.last_error = "Missing " + str(Path.home()) + "/private/runtime.json"
        req = Request(self.base + "/v1/diagnostics", headers={"Authorization": "Bearer " + "a" * 64})
        with urlopen(req, timeout=2) as response:
            data = response.read().decode()
        self.assertNotIn("a" * 64, data)
        self.assertNotIn(str(Path.home()), data)
        self.assertNotIn("token", data)

    def test_diagnostics_preserve_deployment_failure_after_desktop_suspend(self):
        from pathlib import Path
        headers = {'Authorization': 'Bearer ' + 'a' * 64, 'Content-Type': 'application/json'}
        original = 'Renderer timed out at ' + str(Path.home()) + '/private/frame'
        with patch.object(self.server.controller, 'play_card', side_effect=TimeoutError(original)):
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(self.base + '/v1/play-card', headers=headers, data=b'{}'), timeout=2)
        self.assertEqual(rejected.exception.code, 409)
        with urlopen(Request(self.base + '/v1/suspend', headers=headers, data=b'{}'), timeout=2):
            pass
        with urlopen(Request(self.base + '/v1/diagnostics', headers=headers), timeout=2) as response:
            data = json.load(response)
        self.assertEqual(data['error'], '画面或桌面连接中断，已暂停')
        self.assertEqual(data['lastFailure']['path'], '/v1/play-card')
        self.assertEqual(data['lastFailure']['type'], 'TimeoutError')
        self.assertIn('Renderer timed out', data['lastFailure']['error'])
        self.assertNotIn(str(Path.home()), json.dumps(data))
        persisted=json.loads((Path(self.runtime_home.name)/'last-failure.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['type'],'TimeoutError')
        self.assertEqual(persisted['path'],'/v1/play-card')
        self.assertNotIn(str(Path.home()),json.dumps(persisted))

    def test_reconnect_to_ready_engine_clears_pause_error_without_reset_or_resume(self):
        controller = self.server.controller
        controller.engine = FakeEngine()
        original = controller.reset(41)
        controller.suspend('画面或桌面连接中断，已暂停')
        headers = {'Authorization': 'Bearer ' + 'a' * 64, 'Content-Type': 'application/json'}
        with urlopen(Request(self.base + '/v1/start', headers=headers, data=b'{}'), timeout=2) as response:
            status = json.load(response)
        self.assertTrue(status['ready'])
        self.assertFalse(status['playing'])
        self.assertIsNone(status['error'])
        self.assertEqual(controller.engine.reset_count, 1)
        self.assertEqual(controller.observe()['digest'], original['digest'])


if __name__ == "__main__":
    unittest.main()
