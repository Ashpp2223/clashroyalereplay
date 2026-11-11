"""Exercise the frame pipe, freshness and lifecycle without an Android guest."""
import struct
import ctypes
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from bridge.clashback_engine import ClashbackRuntime, ClashbackEngine, AVD, SERIAL
from bridge.clashback_worker import Recorder

POPEN = subprocess.Popen


class ClashbackStartupTest(unittest.TestCase):
    def test_transient_adb_reset_keeps_waiting_for_the_same_device(self):
        runtime = ClashbackRuntime()
        runtime.android = Mock(side_effect=[subprocess.CalledProcessError(1, 'wait-for-device'),
                                            subprocess.CompletedProcess([], 0)])
        runtime._wait_for_device(5)
        self.assertEqual(runtime.android.call_count, 2)

    def exercise(self, console, process_exit=None):
        with tempfile.TemporaryDirectory() as folder:
            runtime = ClashbackRuntime()
            runtime.home = Path(folder)
            runtime.environment_configured = runtime.resources_configured = True
            def android(*args, **kwargs):
                if args[:3] == ('emu', 'avd', 'name'):
                    return subprocess.CompletedProcess([], 0 if console else 1,
                                                       (console or '').encode(), b'')
                raise RuntimeError('stop at device wait')
            runtime.android = Mock(side_effect=android)
            with patch('bridge.clashback_engine.android_sdk', return_value=Path(folder)), \
                 patch('bridge.clashback_engine.subprocess.check_output', return_value=f'{SERIAL} host\n'.encode()), \
                 patch('bridge.clashback_engine.subprocess.Popen') as launch:
                launch.return_value.poll.return_value = process_exit
                try:
                    runtime.start()
                except RuntimeError as error:
                    return str(error), launch.call_count, runtime.android.call_args_list
            self.fail('Startup unexpectedly completed')

    def test_stale_adb_entry_does_not_suppress_emulator_start(self):
        error, launches, _ = self.exercise(None)
        self.assertEqual(error, 'stop at device wait')
        self.assertEqual(launches, 1)

    def test_running_console_is_verified_and_reused_before_adb_wait(self):
        _, launches, calls = self.exercise(AVD + '\nOK\n')
        self.assertEqual(launches, 0)
        self.assertEqual(calls[0].args[:3], ('emu', 'avd', 'name'))

    def test_wrong_device_is_rejected_without_launching(self):
        error, launches, _ = self.exercise('Another_AVD\nOK\n')
        self.assertIn('其他虚拟机', error)
        self.assertEqual(launches, 0)

    def test_exited_emulator_fails_before_a_long_adb_wait(self):
        error, launches, _ = self.exercise(None, process_exit=2)
        self.assertIn('启动失败', error)
        self.assertEqual(launches, 1)


class ClashbackPauseTest(unittest.TestCase):
    def test_native_pause_cancels_the_already_latched_update(self):
        slots = (ctypes.c_float * 8)()
        slots[0], slots[4], slots[5] = 0.05, 1, 0.05
        recorder = Recorder.__new__(Recorder)
        recorder.live = SimpleNamespace(paused=False, dt_slot=ctypes.addressof(slots))
        recorder.synchronize = lambda callback: callback()
        tick = 10
        recorder.observe = lambda: {'tick': tick}
        receipt = recorder.control({'op': 'pause'})
        # The native stub reloads slot[0] AFTER invoking the control callback.
        tick += int(slots[0] / 0.05 + 0.001)
        self.assertEqual(receipt['tick'], tick)
        self.assertTrue(recorder.live.paused)
        self.assertEqual((slots[4], slots[5]), (0, 0))

    def test_pause_publishes_only_the_state_after_the_in_flight_update(self):
        engine = ClashbackEngine(ClashbackRuntime())
        before = {'tick': 10, 'frame': 20}
        # A callback may run before the update has consumed its already-latched
        # dt. The first observation is not a render/update barrier either.
        settled = {'tick': 11, 'frame': 21}
        engine._call = Mock(side_effect=[before, before, settled])
        engine._state = lambda result: result
        self.assertEqual(engine.pause(), settled)
        self.assertEqual([call.args[0] for call in engine._call.call_args_list],
                         ['pause', 'observe', 'observe'])

    def test_pause_barrier_timeout_invalidates_the_session(self):
        engine = ClashbackEngine(ClashbackRuntime())
        engine._call = Mock(return_value={'tick': 10, 'frame': 20})
        engine._state = lambda result: result
        engine.close = Mock()
        with patch('bridge.clashback_engine.time.monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, '暂停'):
                engine.pause()
        engine.close.assert_called_once()


EMITTER = """
import struct, sys
for line in sys.stdin:
    frame, tick = map(int, line.split())
    if frame < 0:
        if frame == -2:
            sys.stdout.buffer.write(b'CRXF' + bytes(100))
            sys.stdout.buffer.flush()
        break
    data = struct.pack('<4sIIIi', b'CRXF', frame, 360, 640, tick).ljust(64, b'\\0')
    data += bytes([frame % 256, 0, 0, 255]) * (360 * 640)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
"""


class ClashbackFramesTest(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen([sys.executable, '-u', '-c', EMITTER],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.runtime = ClashbackRuntime()
        self.runtime.adb = 'test-adb'
        self.runtime.folder = '/opt/clashback/recorder-test'
        self.runtime.android = Mock(side_effect=AssertionError('Do not spawn ADB for each frame'))
        self.launch = patch('bridge.clashback_engine.subprocess.Popen', return_value=self.process).start()

    def tearDown(self):
        self.runtime.close()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        self.process.stdin.close()
        self.process.stdout.close()
        patch.stopall()

    def send(self, frame, tick):
        self.process.stdin.write(f'{frame} {tick}\n'.encode())
        self.process.stdin.flush()

    def test_frames_reuse_one_transport_and_decode_png(self):
        self.send(1, 10)
        first, mime = self.runtime.frame()
        self.assertEqual(mime, 'image/png')
        self.assertTrue(first.startswith(b'\x89PNG\r\n\x1a\n'))
        for _ in range(5):
            self.assertEqual(self.runtime.frame()[0], first)
        self.launch.assert_called_once()
        self.runtime.android.assert_not_called()

    def test_exec_out_passes_python_code_as_a_raw_argument(self):
        # ADB exec-out executes argv directly, unlike adb shell. Exercise the
        # exact -c argument with Python so shell quoting cannot hide in a mock.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'frame.bin'
            header = struct.pack('<4sIIIi', b'CRXF', 1, 360, 640, 10).ljust(64, b'\0')
            path.write_bytes(header + bytes(360 * 640 * 4))
            self.launch.side_effect = lambda command, **kwargs: POPEN(
                [sys.executable, '-u', *command[-3:-1], str(path)], **kwargs)
            try:
                self.assertTrue(self.runtime.frame()[0].startswith(b'\x89PNG'))
                path.unlink()
                self.assertEqual(self.runtime._frame_process.wait(timeout=2), 0)
            finally:
                self.runtime.stop_frames()

    def test_fresh_frame_waits_for_tick_and_newer_render(self):
        self.send(1, 10)
        self.runtime.frame()
        self.runtime.frame_tick, self.runtime.frame_after = 11, 2
        self.send(2, 11)
        self.send(3, 11)
        png, _ = self.runtime.frame(fresh=True)
        import zlib
        offset, pixels = 8, bytearray()
        while offset < len(png):
            size = struct.unpack_from('>I', png, offset)[0]
            if png[offset + 4:offset + 8] == b'IDAT':
                pixels.extend(png[offset + 8:offset + 8 + size])
            offset += size + 12
        self.assertEqual(zlib.decompress(pixels)[1:5], bytes([3, 0, 0, 255]))

    def test_closed_stream_fails_promptly_instead_of_returning_cached_pixels(self):
        self.send(1, 10)
        self.runtime.frame()
        self.send(-1, 0)
        self.process.wait(timeout=3)
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            self.runtime.frame()
        self.assertLess(time.monotonic() - started, 1)

    def test_close_stops_transport_and_invalidates_frame(self):
        self.send(1, 10)
        self.runtime.frame()
        self.runtime.close()
        self.assertIsNotNone(self.process.poll())
        with self.assertRaisesRegex(RuntimeError, '尚未创建对局'):
            self.runtime.frame()

    def test_truncated_frame_is_rejected(self):
        self.send(-2, 0)
        with self.assertRaises(RuntimeError):
            self.runtime.frame()

    def test_waiting_fresh_frame_is_cancelled_when_battle_closes(self):
        self.send(1, 10)
        self.runtime.frame()
        self.runtime.frame_tick, self.runtime.frame_after = 11, 1
        errors = []
        started = threading.Event()

        def wait_for_frame():
            started.set()
            try:
                self.runtime.frame(fresh=True)
            except RuntimeError as error:
                errors.append(str(error))

        thread = threading.Thread(target=wait_for_frame)
        thread.start()
        self.assertTrue(started.wait(1))
        self.runtime.close()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_old_tick_never_satisfies_a_fresh_request(self):
        self.send(3, 10)
        self.runtime.frame()
        self.runtime.frame_tick, self.runtime.frame_after = 11, 2
        with self.assertRaisesRegex(RuntimeError, '尚未就绪'):
            self.runtime.frame(fresh=True)


if __name__ == '__main__':
    unittest.main()
