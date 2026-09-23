"""Decode Android's native H.264 screen stream into the latest JPEG frame."""

from __future__ import annotations

import subprocess
import threading
import time


class NativeFrameStream:
    def __init__(self, adb: str, serial: str):
        self.adb = adb
        self.serial = serial
        self.latest: bytes | None = None
        self.last_frame_at = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._adb_process: subprocess.Popen | None = None
        self._decoder_process: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="native-frame-stream", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def frame(self) -> bytes | None:
        with self._lock:
            if time.monotonic() - self.last_frame_at > 3:
                return None
            return self.latest

    def close(self) -> None:
        self._stop.set()
        for process in (self._decoder_process, self._adb_process):
            if process and process.poll() is None:
                process.kill()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as error:
            self.error = f"视频解码器不可用：{error}"
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        while not self._stop.is_set():
            try:
                self._adb_process = subprocess.Popen(
                    [self.adb, "-s", self.serial, "exec-out", "screenrecord", "--output-format=h264",
                     "--time-limit", "180", "-"], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, creationflags=flags)
                self._decoder_process = subprocess.Popen(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
                     "-f", "h264", "-i", "pipe:0", "-vf", "fps=20", "-q:v", "5",
                     "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                    stdin=self._adb_process.stdout, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, creationflags=flags)
                self._adb_process.stdout.close()
                self._read_frames(self._decoder_process.stdout)
                self.error = "Android 视频流中断，正在重连"
            except Exception as error:
                self.error = str(error)
            finally:
                for process in (self._decoder_process, self._adb_process):
                    if process and process.poll() is None:
                        process.kill()
                self._decoder_process = None
                self._adb_process = None
            if not self._stop.is_set():
                self._stop.wait(1)

    def _read_frames(self, stream) -> None:
        pending = bytearray()
        while not self._stop.is_set():
            chunk = stream.read(32768)
            if not chunk:
                return
            pending.extend(chunk)
            while True:
                start = pending.find(b"\xff\xd8")
                if start < 0:
                    pending.clear()
                    break
                if start:
                    del pending[:start]
                end = pending.find(b"\xff\xd9", 2)
                if end < 0:
                    if len(pending) > 8 * 1024 * 1024:
                        pending.clear()
                    break
                frame = bytes(pending[:end + 2])
                del pending[:end + 2]
                with self._lock:
                    self.latest = frame
                    self.last_frame_at = time.monotonic()
                    self.error = None
