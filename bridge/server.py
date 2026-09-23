"""Loopback-only authenticated HTTP API for the native battle bridge."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
from urllib.parse import urlparse

from .controller import BattleController
from .runtime import AndroidRuntime


ALLOWED_ORIGINS = {"http://127.0.0.1:1420", "http://localhost:1420", "tauri://localhost",
                   "https://tauri.localhost"}


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str, controller: BattleController):
        self.token = token
        self.controller = controller
        self.start_thread: threading.Thread | None = None
        super().__init__(("127.0.0.1", port), BridgeRequest)


class BridgeRequest(BaseHTTPRequestHandler):
    server: BridgeServer

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
        return secrets.compare_digest(self.headers.get("Authorization", ""),
                                      "Bearer " + self.server.token)

    def _send(self, status: int, body: dict | bytes, content_type: str = "application/json") -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin") not in ALLOWED_ORIGINS:
            self._send(403, {"error": "origin not allowed"})
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            if path == "/v1/status":
                self._send(200, self.server.controller.status())
            elif path == "/v1/observe":
                self._send(200, self.server.controller.observe())
            elif path == "/v1/frame":
                frame, format_name = self.server.controller.runtime.frame()
                self._send(200, frame, format_name)
            else:
                self._send(404, {"error": "not found"})
        except Exception as error:
            self._send(409, {"error": str(error)})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1024 * 1024:
                raise ValueError("请求体过大")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("请求体必须是对象")
            path = urlparse(self.path).path
            controller = self.server.controller
            if path == "/v1/start":
                if not self.server.start_thread or not self.server.start_thread.is_alive():
                    self.server.start_thread = threading.Thread(target=self._start_runtime, daemon=True)
                    self.server.start_thread.start()
                result = controller.status()
            elif path == "/v1/reset":
                result = controller.reset(body.get("seed", 1))
            elif path == "/v1/pause":
                result = controller.pause()
            elif path == "/v1/resume":
                result = controller.resume()
            elif path == "/v1/speed":
                result = controller.speed(body.get("multiplier"))
            elif path == "/v1/step":
                result = controller.step(body.get("ticks", 1))
            elif path == "/v1/play-card":
                result = controller.play_card(body.get("owner"), body.get("slot"),
                                              body.get("x"), body.get("y"))
            elif path == "/v1/snapshot-create":
                result = controller.snapshot_create()
            elif path == "/v1/snapshot-restore":
                result = controller.snapshot_restore(body.get("handle"))
            elif path == "/v1/snapshot-release":
                result = controller.snapshot_release(body.get("handle"))
            elif path == "/v1/replay":
                result = controller.replay(body.get("seed"), body.get("actions", []),
                                           body.get("targetTick"), body.get("expectedDigest"))
            else:
                self._send(404, {"error": "not found"})
                return
            self._send(200, result)
        except (ValueError, KeyError, TypeError) as error:
            self._send(400, {"error": str(error)})
        except Exception as error:
            self._send(409, {"error": str(error)})

    def _start_runtime(self) -> None:
        try:
            self.server.controller.start()
        except Exception:
            pass  # status exposes the stage and sanitized failure to the UI.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    arguments = parser.parse_args()
    token = os.environ.get("CR_BRIDGE_TOKEN")
    if not token or len(token) < 32:
        raise SystemExit("CR_BRIDGE_TOKEN is required")
    server = BridgeServer(arguments.port, token, BattleController(AndroidRuntime()))
    print(json.dumps({"port": server.server_address[1], "protocol": "native-bridge.v1"}), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.controller.close()
        server.server_close()


if __name__ == "__main__":
    main()
