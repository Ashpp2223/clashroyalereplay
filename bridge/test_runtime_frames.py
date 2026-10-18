"""Fresh screenshots must preserve pixels and support older screencap headers."""
import struct
import subprocess
import unittest
from unittest.mock import patch

import imageio_ffmpeg

from bridge.runtime import AndroidRuntime, rgba_png


class RuntimeFrameTest(unittest.TestCase):
    def setUp(self):
        self.runtime = AndroidRuntime.__new__(AndroidRuntime)
        self.runtime.adb = "test-adb"
        self.runtime.serial = "test-device"

    def test_fresh_capture_preserves_rgba_pixels_with_both_android_headers(self):
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 255,
                        0, 0, 255, 127, 20, 40, 60, 0])
        run = subprocess.run
        for header in (struct.pack("<III", 2, 2, 1), struct.pack("<IIII", 2, 2, 1, 1)):
            with self.subTest(header_bytes=len(header)):
                def capture(command, **kwargs):
                    if command[0] == "test-adb":
                        self.assertEqual(command[-2:], ['exec-out', 'screencap'])
                        return subprocess.CompletedProcess(command, 0, header + pixels, b"")
                    return run(command, **kwargs)
                with patch("bridge.runtime.subprocess.run", side_effect=capture):
                    png = self.runtime.frame_png()
                decoded = run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error",
                               "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                              input=png, capture_output=True, check=True, timeout=5)
                self.assertEqual(decoded.stdout, pixels)

    def test_png_compression_levels_keep_the_same_pixels(self):
        raw = struct.pack('<IIII', 2, 2, 1, 1) + bytes(range(16))
        for level in (0, 1):
            png = rgba_png(raw, compression=level)
            decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error',
                                      '-i', 'pipe:0', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'],
                                     input=png, capture_output=True, check=True, timeout=5)
            self.assertEqual(decoded.stdout, raw[16:])

    def test_invalid_dimensions_and_pixel_layout_are_rejected(self):
        for width, height, fmt, pixels in [(0, 2, 1, b''), (9000, 1, 1, b''), (2, 2, 4, bytes(16)), (2, 2, 1, bytes(8))]:
            with self.subTest(width=width, height=height, fmt=fmt):
                with self.assertRaises(ValueError):
                    rgba_png(struct.pack('<III', width, height, fmt) + pixels)

    def test_unsupported_or_broken_raw_capture_uses_device_png(self):
        png = b"\x89PNG\r\n\x1a\noriginal-device-frame"
        for raw in (b"broken-capture", struct.pack("<IIII", 2, 2, 4, 1) + bytes(16),
                    struct.pack("<IIII", 2, 2, 1, 1) + bytes(8)):
            with self.subTest(raw=raw):
                replies = [subprocess.CompletedProcess([], 0, raw),
                           subprocess.CompletedProcess([], 0, png)]
                with patch("bridge.runtime.subprocess.run", side_effect=replies):
                    self.assertEqual(self.runtime.frame_png(), png)

    def test_raw_adb_failure_falls_back_and_bad_png_is_rejected(self):
        png = b'\x89PNG\r\n\x1a\nvalid-fallback'
        for fallback, valid in ((png, True), (b'not-a-png', False)):
            with self.subTest(valid=valid):
                replies = [subprocess.CalledProcessError(1, 'screencap'),
                           subprocess.CompletedProcess([], 0, fallback)]
                with patch('bridge.runtime.subprocess.run', side_effect=replies) as run:
                    if valid:
                        self.assertEqual(self.runtime.frame_png(), png)
                    else:
                        with self.assertRaisesRegex(RuntimeError, '有效画面'):
                            self.runtime.frame_png()
                self.assertEqual(run.call_args.args[0][-2:], ['screencap', '-p'])

    def test_capture_requires_a_device(self):
        self.runtime.serial = None
        with self.assertRaisesRegex(RuntimeError, "模拟器未启动"):
            self.runtime.frame_png()
