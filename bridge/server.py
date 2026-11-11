"""Loopback-only authenticated HTTP API for the native battle bridge."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import sys
import time
from pathlib import Path
from .paths import RUNTIME_HOME
from urllib.parse import urlparse, parse_qs

from .controller import BattleController
from .runtime import AndroidRuntime
from .engine_adapter import new_engine, new_runtime
from .setup import SetupManager


ALLOWED_ORIGINS = {"http://127.0.0.1:1420", "http://localhost:1420", "tauri://localhost",
                   "http://tauri.localhost", "https://tauri.localhost"}


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str, controller: BattleController):
        self.token = token
        self.controller = controller
        self.setup = SetupManager()
        self.start_thread: threading.Thread | None = None
        self.last_contact = time.monotonic()
        self.last_failure: dict | None = None
        self._last_keepalive = time.monotonic()
        self._stopped = threading.Event()
        super().__init__(("127.0.0.1", port), BridgeRequest)
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def _watch(self):
        while not self._stopped.wait(0.02):
            if self.controller.playing and time.monotonic() - self.last_contact > 5:
                self.controller.suspend("桌面连接中断，战斗已暂停")
            try:
                self.controller.advance_playback()
            except Exception as error:
                self.record_failure('/v1/playback',error)
            if time.monotonic() - self._last_keepalive >= 5:
                self._last_keepalive = time.monotonic()
                try:
                    self.controller.keepalive()
                except Exception as error:
                    self.record_failure('/native/keepalive', error)

    def record_failure(self, path: str, error: Exception) -> None:
        self.last_failure = {
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "path": path, "type": type(error).__name__, "error": str(error)[:2000],
        }
        failure = dict(self.last_failure)
        for private in (self.token, str(RUNTIME_HOME), str(Path.home())):
            failure['error'] = failure['error'].replace(private, '<已隐藏>').replace(private.replace('\\', '/'), '<已隐藏>')
        temporary = RUNTIME_HOME / f'last-failure.{threading.get_ident()}.tmp'
        try:
            RUNTIME_HOME.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(failure, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, RUNTIME_HOME / 'last-failure.json')
        except OSError:
            pass

    def server_close(self):
        self._stopped.set()
        super().server_close()


class BridgeRequest(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"
    # Reuse the connection without delayed ACK stalls between headers and pixels.
    disable_nagle_algorithm = True

    def log_message(self, _format: str, *_args) -> None:
        return  # Avoid printing credentials or local paths from requests.

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _authorized(self) -> bool:
        valid = secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token)
        if valid:
            self.server.last_contact = time.monotonic()
        return valid

    def _send(self, status: int, body: dict | bytes, content_type: str = "application/json") -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The UI cancels obsolete frame requests on pause/seek. A closed
            # response socket is not a failed capture; the watchdog covers exit.
            self.close_connection = True

    def _record_failure(self, path: str, error: Exception) -> None:
        # Preserve the actual failure before the desktop's subsequent suspend
        # request replaces controller.last_error with a generic pause reason.
        self.server.record_failure(path, error)

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin") not in ALLOWED_ORIGINS:
            self._send(403, {"error": "origin not allowed"})
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            if path == "/v1/status":
                status = self.server.controller.status()
                status["setup"] = self.server.setup.status.copy()
                status["environmentConfigured"] = getattr(self.server.controller.runtime,"environment_configured",(RUNTIME_HOME / "runtime.json").is_file())
                status["resourcesConfigured"] = getattr(self.server.controller.runtime,"resources_configured",(RUNTIME_HOME / "prepared.json").is_file())
                self._send(200, status)
            elif path == "/v1/diagnostics":
                status = self.server.controller.status()
                error = str(status.get("error") or "")
                stream = self.server.controller.runtime.video if self.server.controller.runtime else None
                video_error = str(stream.error or "") if stream else ""
                failure = dict(self.server.last_failure) if self.server.last_failure else None
                for private in (str(RUNTIME_HOME), str(Path.home())):
                    error = error.replace(private, "<本机目录>").replace(private.replace("\\", "/"), "<本机目录>")
                    video_error = video_error.replace(private, "<本机目录>").replace(private.replace("\\", "/"), "<本机目录>")
                    if failure:
                        failure['error'] = failure['error'].replace(private, "<本机目录>").replace(private.replace("\\", "/"), "<本机目录>")
                self._send(200, {"schema": "cr-native-diagnostics.v1", "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  "stage": status["stage"], "ready": status["ready"], "playing": status["playing"],
                                  "error": error[:1000], "identity": status["identity"], "capabilities": status["capabilities"],
                                  "lastFailure": failure,
                                  "video": {"ready": bool(stream and stream.frame()), "error": video_error[:1000],
                                            "framesReceived": stream.frames_received if stream else 0,
                                            "sessionsStarted": stream.sessions_started if stream else 0}})
            elif path == "/v1/observe":
                self._send(200, self.server.controller.observe())
            elif path == "/v1/frame":
                frame_runtime = self.server.controller.runtime
                frame_session = getattr(frame_runtime, 'folder', None)
                if parse_qs(urlparse(self.path).query).get('fresh') == ['1']:
                    frame, format_name = frame_runtime.frame(fresh=True)
                else:
                    frame, format_name = frame_runtime.frame()
                self._send(200, frame, format_name)
            else:
                self._send(404, {"error": "not found"})
        except Exception as error:
            controller = self.server.controller
            with controller.lock:
                # A cancelled old capture must not suspend a newly reset battle.
                # Check under the same lock as reset and suspend, after any
                # pending battle replacement has completed.
                obsolete = path == '/v1/frame' and (
                    frame_runtime is not controller.runtime or
                    frame_session != getattr(frame_runtime, 'folder', None))
                if not obsolete:
                    self._record_failure(path, error)
                    if path in ("/v1/frame", "/v1/observe"):
                        controller.suspend("画面或战斗连接中断，已暂停")
            self._send(409, {"error": str(error)})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1024 * 1024:
                raise ValueError("请求体过大")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("请求体必须是对象")
            controller = self.server.controller
            if path.startswith('/v1/setup/') and getattr(controller.runtime,'profile',None):
                raise ValueError('当前国服配置使用已验证的研究目录，无需运行旧版安装器')
            if path == "/v1/start":
                if self.server.setup.status["state"] in ("installing", "importing"):
                    raise ValueError("请等待运行环境准备完成")
                if controller.engine is not None:
                    # Explicit reconnect clears the prior suspension before its
                    # response. No native state is changed or resumed here.
                    controller.start()
                elif not self.server.start_thread or not self.server.start_thread.is_alive():
                    self.server.start_thread = threading.Thread(target=self._start_runtime, daemon=True)
                    self.server.start_thread.start()
                result = controller.status()
            elif path == "/v1/setup/install":
                directory = body.get("directory")
                if directory is not None and not isinstance(directory, str):
                    raise ValueError("安装目录无效")
                result = self.server.setup.start(body.get("acceptLicense"), directory)
            elif path == "/v1/setup/resources":
                if self.server.start_thread and self.server.start_thread.is_alive():
                    raise ValueError("请等待当前连接完成")
                if not isinstance(body.get("path"), str):
                    raise ValueError("请选择战斗资源包")
                result = self.server.setup.import_resources(body["path"], controller)
            elif path == "/v1/reset":
                result = controller.reset(body.get("seed", 1), body.get("deck0"), body.get("deck1"),body.get("forms0"),body.get("forms1"),body.get("level"),body.get("dealMode"),body.get("tower0"),body.get("tower1"))
            elif path == "/v1/pause":
                result = controller.pause()
            elif path == "/v1/suspend":
                result = controller.suspend("画面或桌面连接中断，已暂停")
            elif path == "/v1/shutdown":
                result = {"stopping": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/v1/resume":
                result = controller.resume()
            elif path == "/v1/playback":
                result = controller.start_playback(body.get('actions',[]),body.get('targetTick'),
                    body.get('expectedTick'),body.get('baseDigest'),body.get('expectedDigest'))
            elif path == "/v1/speed":
                result = controller.speed(body.get("multiplier"))
            elif path == "/v1/step":
                result = controller.step(body.get("ticks", 1),body.get("expectedTick"))
            elif path == "/v1/play-card":
                result = controller.play_card(body.get("owner"), body.get("slot"),
                                              body.get("x"), body.get("y"),body.get("expectedTick"))
            elif path == '/v1/activate-ability':
                result = controller.activate_ability(body.get('owner'),body.get('entityId'),body.get('abilityId'))
            elif path == "/v1/snapshot-create":
                result = controller.snapshot_create()
            elif path == "/v1/snapshot-restore":
                result = controller.snapshot_restore(body.get("handle"))
            elif path == "/v1/snapshot-release":
                result = controller.snapshot_release(body.get("handle"))
            elif path == "/v1/replay":
                result = controller.replay(body.get("seed"), body.get("actions", []),
                                           body.get("targetTick"), body.get("expectedDigest"),
                                           body.get("snapshotHandle"), body.get("baseTick"),
                                           body.get("baseDigest"), body.get("deck0"), body.get("deck1"),body.get("forms0"),body.get("forms1"),body.get("level"),body.get("dealMode"),body.get("tower0"),body.get("tower1"),
                                           checkpoints=body.get("checkpoints"))
            else:
                self._send(404, {"error": "not found"})
                return
            self._send(200, result)
        except (ValueError, KeyError, TypeError) as error:
            self._send(400, {"error": str(error)})
        except Exception as error:
            self._record_failure(path, error)
            self._send(409, {"error": str(error)})

    def _start_runtime(self) -> None:
        try:
            self.server.controller.start()
        except Exception:
            pass  # status exposes the stage and sanitized failure to the UI.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--parent-stdin", action="store_true")
    arguments = parser.parse_args()
    token = os.environ.get("CR_BRIDGE_TOKEN")
    if not token or len(token) < 32:
        raise SystemExit("CR_BRIDGE_TOKEN is required")
    runtime = new_runtime()
    server = BridgeServer(arguments.port, token, BattleController(runtime, getattr(runtime, "create_engine", new_engine)))
    print(json.dumps({"port": server.server_address[1], "protocol": "native-bridge.v1"}), flush=True)
    if arguments.parent_stdin:
        def parent_watch():
            # EOF also handles the desktop process crashing or being terminated.
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
            server.shutdown()
        threading.Thread(target=parent_watch, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.controller.close()
        server.server_close()


if __name__ == "__main__":
    main()
