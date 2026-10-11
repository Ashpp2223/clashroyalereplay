"""Fresh screenshots must preserve pixels and support older screencap headers."""
import gzip
import struct
import subprocess
import unittest
from unittest.mock import patch

import imageio_ffmpeg

from bridge.runtime import AndroidRuntime


class RuntimeFrameTest(unittest.TestCase):
    def setUp(self):
        self.runtime = AndroidRuntime.__new__(AndroidRuntime)
        self.runtime.adb = "test-adb"
        self.runtime.serial = "test-device"
        self.runtime.frame_encoder = imageio_ffmpeg.get_ffmpeg_exe()

    def test_fresh_capture_preserves_rgba_pixels_with_both_android_headers(self):
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 255,
                        0, 0, 255, 255, 20, 40, 60, 255])
        run = subprocess.run
        for header in (struct.pack("<III", 2, 2, 1), struct.pack("<IIII", 2, 2, 1, 1)):
            with self.subTest(header_bytes=len(header)):
                def capture(command, **kwargs):
                    if command[0] == "test-adb":
                        return subprocess.CompletedProcess(command, 0, gzip.compress(header + pixels), b"")
                    return run(command, **kwargs)
                with patch("bridge.runtime.subprocess.run", side_effect=capture):
                    png = self.runtime.frame_png()
                decoded = run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error",
                               "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                              input=png, capture_output=True, check=True, timeout=5)
                self.assertEqual(decoded.stdout, pixels)

    def test_unsupported_or_broken_raw_capture_uses_device_png(self):
        png = b"\x89PNG\r\n\x1a\noriginal-device-frame"
        for raw in (b"broken-gzip", gzip.compress(struct.pack("<IIII", 2, 2, 4, 1) + bytes(16)),
                    gzip.compress(struct.pack("<IIII", 2, 2, 1, 1) + bytes(8))):
            with self.subTest(raw=raw):
                replies = [subprocess.CompletedProcess([], 0, raw),
                           subprocess.CompletedProcess([], 0, png)]
                with patch("bridge.runtime.subprocess.run", side_effect=replies):
                    self.assertEqual(self.runtime.frame_png(), png)

    def test_capture_requires_a_device(self):
        self.runtime.serial = None
        with self.assertRaisesRegex(RuntimeError, "模拟器未启动"):
            self.runtime.frame_png()
