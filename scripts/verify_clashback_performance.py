"""Compare frame transports and exercise a disposable Clashback battle."""
import argparse
import json
from pathlib import Path
import statistics
import struct
import time

from bridge.clashback_engine import ClashbackRuntime, GUEST, FRAME_SIZE
from bridge.controller import BattleController
from bridge.runtime import rgba_png


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--restore-cycles', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 5:
        parser.error('--seconds must be at least 5')
    runtime = ClashbackRuntime()
    controller = BattleController(runtime, runtime.create_engine)
    report = {'status': 'running', 'transports': {}, 'checks': []}

    def timed(fn):
        started = time.perf_counter()
        value = fn()
        return value, round((time.perf_counter() - started) * 1000, 3)

    try:
        _, report['startupMs'] = timed(controller.start)
        # Do not attach to or compete with an existing user battle.
        processes = runtime.android('shell', 'ps', '-A', '-o', 'ARGS').stdout.decode(errors='replace')
        if '/opt/clashback/clashback_worker.py' in processes:
            raise RuntimeError('Close the existing Clashback battle before this isolated check')
        root, report['resetMs'] = timed(lambda: controller.reset(41))
        base = controller.snapshot_create()
        print('Initialized disposable battle', flush=True)
        for mode in ('polling', 'stream'):
            controller.snapshot_restore(base['handle'])
            controller.resume()
            samples, frames = [], set()
            started = time.monotonic()
            next_progress = started + 10
            while time.monotonic() - started < args.seconds:
                begin = time.perf_counter()
                if mode == 'polling':
                    data = runtime.android('exec-out', 'cat', f'{GUEST}{runtime.folder}/live/frame.bin').stdout
                    assert len(data) == FRAME_SIZE and data[:4] == b'CRXF'
                    stride = 360 * 4
                    pixels = b''.join(data[pos:pos + stride] for pos in range(len(data) - stride, 63, -stride))
                    png = rgba_png(struct.pack('<III', 360, 640, 1) + pixels)
                else:
                    png, _ = runtime.frame()
                    data = runtime._frame_data
                assert png.startswith(b'\x89PNG\r\n\x1a\n')
                frames.add(struct.unpack_from('<I', data, 4)[0])
                samples.append((time.perf_counter() - begin) * 1000)
                if len(samples) % 10 == 0:
                    controller.observe()
                if time.monotonic() >= next_progress:
                    print(mode, len(frames), 'distinct frames', flush=True)
                    next_progress += 10
                time.sleep(max(0, 1 / 30 - (time.perf_counter() - begin)))
            elapsed = time.monotonic() - started
            end = controller.pause()
            report['transports'][mode] = {
                'seconds': round(elapsed, 2), 'requests': len(samples), 'distinctFrames': len(frames),
                'distinctFps': round(len(frames) / elapsed, 2), 'medianMs': round(statistics.median(samples), 3),
                'p95Ms': round(sorted(samples)[int(len(samples) * .95)], 3),
                'maxMs': round(max(samples), 3), 'lastTick': end['tick']}
            print(mode, report['transports'][mode], flush=True)
        for index in range(args.restore_cycles):
            restored = controller.snapshot_restore(base['handle'])
            if restored['digest'] != root['digest']:
                report['mismatch'] = {'iteration': index, 'expected': root, 'actual': restored}
                raise AssertionError('Snapshot restore changed the confirmed root state')
            stepped = controller.step(1)
            assert stepped['tick'] == root['tick'] + 1
            runtime.frame(fresh=True)
        report['checks'].append(f'{args.restore_cycles} snapshot restores, exact single steps and fresh frames')
        controller.snapshot_restore(base['handle'])
        controller.step(40)
        expected = controller.observe()['digest']
        controller.snapshot_restore(base['handle'])
        assert controller.step(40)['digest'] == expected
        report['checks'].append('Snapshot suffix replay retains the same digest')
        pause_samples = []
        for index in range(60):
            controller.resume()
            time.sleep(0.08)
            paused, pause_ms = timed(controller.pause)
            # Read a later native update, not the controller's paused cache.
            confirmed = controller.engine._capture()
            assert paused['tick'] == confirmed.tick, (paused['tick'], confirmed.tick)
            runtime.frame(fresh=True)
            pause_samples.append(pause_ms)
        report['pause'] = {'cycles': len(pause_samples),
                           'medianMs': round(statistics.median(pause_samples), 3),
                           'maxMs': max(pause_samples), 'tickDrifts': 0}
        report['checks'].append('60 resume/pause cycles retain the confirmed tick and fresh frame')
        old_process = runtime._frame_process
        controller.reset(42)
        runtime.frame(fresh=True)
        assert old_process.poll() is not None and runtime._frame_process is not old_process
        report['checks'].append('New battle stops the old frame pipe and displays a fresh frame')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        controller.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
