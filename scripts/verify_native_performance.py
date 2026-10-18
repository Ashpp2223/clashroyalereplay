"""Measure startup and node display on the dedicated emulator-5560 test runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import gzip
import statistics
import struct
import subprocess
import zlib
from time import perf_counter


def profile_runtime_verification(runtime):
    """Compare identical full verification with remote/local fallback extraction."""
    from bridge.runtime import sha256
    prepared = json.loads((Path(os.environ['CR_RUNTIME_HOME']) / 'prepared.json').read_text(encoding='utf-8-sig'))
    source = Path(prepared['path'])
    assert sha256(source) == runtime.apk_sha256
    samples = []
    for mode in ('remote', 'local', 'local', 'remote', 'remote', 'local'):
        runtime._verified_apk = source if mode == 'local' else None
        started = perf_counter()
        runtime._check_installed_runtime()
        sample = {'mode':mode,'ms':round((perf_counter()-started)*1000,2),
                  'fingerprint':runtime.runtime_fingerprint_sha256}
        samples.append(sample)
        print('verification: ' + json.dumps(sample), flush=True)
    assert len({sample['fingerprint'] for sample in samples}) == 1
    return {'samples':samples,'medianMs':{
        mode:round(statistics.median(sample['ms'] for sample in samples if sample['mode']==mode),2)
        for mode in ('remote','local')},'completeInstalledApkHashRetained':True}


def profile_frame_capture(runtime, output):
    """Alternate transports on one paused battle; measure host encoding separately."""
    from bridge.runtime import rgba_png
    samples = []
    commands = {
        'gzip': ['exec-out', 'sh', '-c', 'screencap | gzip -1'],
        'raw': ['exec-out', 'screencap'],
        'android-png': ['exec-out', 'screencap', '-p'],
    }
    raw = None
    for index in range(3):
        for mode in (list(commands) if index % 2 == 0 else list(reversed(commands))):
            started = perf_counter()
            capture = subprocess.run([str(runtime.adb), '-s', runtime.serial, *commands[mode]],
                                     capture_output=True, timeout=10, check=True,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            captured = perf_counter()
            pixels = gzip.decompress(capture.stdout) if mode == 'gzip' else capture.stdout
            decoded = perf_counter()
            item = {'mode': mode, 'captureMs': round((captured - started) * 1000, 2),
                    'decompressMs': round((decoded - captured) * 1000, 2), 'transferBytes': len(capture.stdout)}
            if mode != 'android-png':
                raw = pixels
                width, height, fmt = struct.unpack_from('<III', pixels)
                assert (width, height, fmt) == (1080, 1920, 1)
                for level in (1, 0):
                    started_encode = perf_counter()
                    image = rgba_png(pixels, compression=level)
                    item[f'png{level}Ms'] = round((perf_counter() - started_encode) * 1000, 2)
                    item[f'png{level}Bytes'] = len(image)
                    # Verify this exact capture, independent of Android animations.
                    rows = zlib.decompress(image[41:-16])
                    stride = width * 4
                    header = len(pixels) - stride * height
                    assert b''.join(rows[y * (stride + 1) + 1:(y + 1) * (stride + 1)] for y in range(height)) == pixels[header:]
            else:
                assert pixels.startswith(b'\x89PNG\r\n\x1a\n')
            samples.append(item)
            print('capture: ' + json.dumps(item), flush=True)
    output.with_suffix('.raw').write_bytes(raw)
    for level in (0, 1):
        output.with_name(output.stem + f'-png{level}.png').write_bytes(rgba_png(raw, compression=level))
    return {'samples': samples, 'medianCaptureMs': {
        mode: round(statistics.median(sample['captureMs'] for sample in samples if sample['mode'] == mode), 2)
        for mode in commands}, 'verifiedLossless': True}


def verify_checkpoint_cache(controller, output):
    """Compare a first ancestor visit with/without route snapshots, including pixels."""
    root = controller.reset(41)
    root_snapshot = {**controller.snapshot_create(), 'digest': root['digest']}
    card = next(card for card in root['players'][1]['hand'] if card['cost'] * 10000 <= root['players'][1]['elixir'])
    played = controller.play_card(1, card['slot'], 4500, 24000)
    assert played['accepted']
    actions = [played['action']]
    checkpoints = []
    controller.speed(4)
    for tick in (100, 160, 240, 320):
        state = controller.step(tick - controller.observe()['tick'])
        checkpoints.append({'nodeId': f'node-{tick}', 'tick': tick, 'digest': state['digest']})
    controller.speed(1)
    target, ancestor = checkpoints[-1], checkpoints[-2]

    def restore(snapshot, checkpoint):
        return controller.replay(41, actions if snapshot['tick'] == root['tick'] else [],
                                 checkpoint['tick'], checkpoint['digest'], snapshot['handle'],
                                 snapshot['tick'], snapshot.get('digest', root['digest']))

    started = perf_counter()
    restore(root_snapshot, target)
    uncached_open_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    restore(root_snapshot, ancestor)
    uncached_state_ms = (perf_counter() - started) * 1000
    image, _ = controller.runtime.frame(fresh=True)
    assert image.startswith(b'\x89PNG\r\n\x1a\n')
    uncached_display_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    result = controller.replay(41, actions, target['tick'], target['digest'], root_snapshot['handle'],
                               root['tick'], root['digest'], checkpoints=checkpoints)
    cached_open_ms = (perf_counter() - started) * 1000
    assert len(result['snapshots']) == len(checkpoints)
    receipts = {snapshot['nodeId']: snapshot for snapshot in result['snapshots']}
    checks = []
    for checkpoint in checkpoints:
        started = perf_counter()
        state = restore(receipts[checkpoint['nodeId']], checkpoint)
        state_ms = (perf_counter() - started) * 1000
        assert state['tick'] == checkpoint['tick'] and state['digest'] == checkpoint['digest']
        image, _ = controller.runtime.frame(fresh=True)
        assert image.startswith(b'\x89PNG\r\n\x1a\n')
        checks.append({'tick': state['tick'], 'digest': state['digest'],
                       'stateMs': round(state_ms, 2), 'withFreshFrameMs': round((perf_counter() - started) * 1000, 2)})
    output.with_name(output.stem + '-checkpoint.png').write_bytes(image)
    native = controller.engine.request('status')
    assert native['paused'] and native['speed'] == 1
    for snapshot in result['snapshots'] + [root_snapshot]:
        controller.snapshot_release(snapshot['handle'])
    return {'openWithoutCheckpointsMs': round(uncached_open_ms, 2), 'openWithCheckpointsMs': round(cached_open_ms, 2),
            'firstAncestorWithoutCache': {'tick': ancestor['tick'], 'stateMs': round(uncached_state_ms, 2),
                                          'withFreshFrameMs': round(uncached_display_ms, 2)},
            'firstVisitsWithCache': checks, 'verifiedCheckpoints': len(checks), 'playbackSpeedRestored': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-device", action="store_true")
    parser.add_argument("--frame-capture-only", action="store_true")
    parser.add_argument("--startup-only", action="store_true")
    parser.add_argument("--verification-only", action="store_true")
    parser.add_argument("--reopen-only", action="store_true")
    parser.add_argument("--cold-first", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads((args.runtime / "runtime.json").read_text(encoding="utf-8-sig"))
    if config.get("serial") != "emulator-5560":
        raise ValueError("Performance checks require the dedicated emulator-5560")
    os.environ["CR_RUNTIME_HOME"] = str(args.runtime.resolve())
    os.environ["CR_ANDROID_SERIAL"] = "emulator-5560"
    os.environ["CR_ENGINE_PORT"] = "26790"
    from bridge.runtime import AndroidRuntime
    from bridge.controller import BattleController
    from bridge import bootstrap_runtime, install_runtime

    runtime = AndroidRuntime()
    controller = BattleController(runtime)
    report = {"status": "running", "timingsMs": {}, "startupStages": [], "screenshotsMs": [], "switchesMs": []}
    report['startupCommands'] = []

    def timed(label, work):
        started = perf_counter()
        result = work()
        elapsed = round((perf_counter() - started) * 1000, 2)
        report["timingsMs"][label] = elapsed
        print(f"{label}: {elapsed} ms", flush=True)
        return result

    original_command = runtime.command
    def command(*values, **kwargs):
        started = perf_counter()
        try:
            return original_command(*values, **kwargs)
        finally:
            label = ' '.join(str(value) if not str(value).startswith('/') else Path(str(value)).name for value in values[:4])
            report['startupCommands'].append({'kind':'runtime','command':label,'ms':round((perf_counter()-started)*1000,2)})
    runtime.command = command
    original_shell = install_runtime.Device.shell
    def shell(device, script, **kwargs):
        started = perf_counter()
        try:
            return original_shell(device, script, **kwargs)
        finally:
            label = 'unzip-fallback' if script.startswith('unzip ') else 'resource-hashes' if 'find .' in script else script.split()[0]
            report['startupCommands'].append({'kind':'resources','command':label,'ms':round((perf_counter()-started)*1000,2)})
    install_runtime.Device.shell = shell
    original_bootstrap = bootstrap_runtime.bootstrap
    def bootstrap(*values, **kwargs):
        started = perf_counter()
        try:
            return original_bootstrap(*values, **kwargs)
        finally:
            report['startupStages'].append({'stage':'bootstrap','ms':round((perf_counter()-started)*1000,2)})
    bootstrap_runtime.bootstrap = bootstrap

    first_launch = True
    for name in ("_launch_sdk_emulator", "_wait_for_avd", "_wait_for_boot", "_check_installed_runtime", "_check_updated_content", "_attest"):
        original = getattr(runtime, name)
        def instrument(*values, _original=original, _name=name, **kwargs):
            nonlocal first_launch
            if _name == '_launch_sdk_emulator' and first_launch:
                first_launch = False
                if args.cold_first:
                    kwargs['cold_boot'] = True
            started = perf_counter()
            try:
                return _original(*values, **kwargs)
            finally:
                elapsed = round((perf_counter() - started) * 1000, 2)
                report["startupStages"].append({"stage": _name, "ms": elapsed})
                print(f"{_name}: {elapsed} ms", flush=True)
        setattr(runtime, name, instrument)
    device_process = None
    try:
        if args.verification_only:
            runtime.serial = runtime._find_arm64_device()
            if runtime.serial != 'emulator-5560':
                raise ValueError('Verification comparison requires the already booted test emulator')
            report['verificationComparison'] = profile_runtime_verification(runtime)
            report['status'] = 'passed'
            return
        timed("startup", controller.start)
        device_process = runtime.process
        if args.reopen_only:
            report['firstConnection'] = {'apkCacheHit': runtime.apk_cache_hit, 'resumedGame': runtime.resumed_game}
            identity = controller.status()['identity']
            root = timed('firstBattle', lambda: controller.reset(41))
            snapshot = controller.snapshot_create()
            card = next(card for card in root['players'][1]['hand'] if card['cost'] * 10000 <= root['players'][1]['elixir'])
            played = controller.play_card(1, card['slot'], 4500, 24000)
            assert played['accepted']
            child = controller.step(9)
            timed('shutdownWithWarmGame', controller.close)
            assert runtime.process is None
            device_process = None
            timed('reopenConnection', controller.start)
            device_process = runtime.process
            report['reopenedConnection'] = {'apkCacheHit': runtime.apk_cache_hit, 'resumedGame': runtime.resumed_game}
            assert runtime.apk_cache_hit, 'Unchanged APK verification was not reused'
            assert runtime.resumed_game, 'Quick Boot did not resume the initialized game'
            assert controller.status()['identity'] == identity
            resumed_status = controller.engine.request('status')
            assert resumed_status['paused']
            assert resumed_status['snapshotHandles'] == 0
            reopened = timed('reopenSavedBattle', lambda: controller.replay(41, [played['action']], child['tick'], child['digest']))
            assert reopened['digest'] == child['digest']
            report['verifiedDigest'] = reopened['digest']
            report['oldHandleReleased'] = snapshot['handle'] not in controller._snapshot_handles
            assert report['oldHandleReleased']
            picture = timed('reopenFrame', runtime.frame_png)
            assert picture.startswith(b'\x89PNG\r\n\x1a\n')
            args.output.with_suffix('.png').write_bytes(picture)
            device_process = runtime.process
            report['status'] = 'passed'
            return
        runtime.process = None  # Keep the same booted test device for warm restart comparison.
        if args.startup_only:
            report['identity'] = controller.status()['identity']
            root = timed('firstBattle', lambda: controller.reset(41))
            assert root['tick'] == 90
            controller.close()
            timed('warmStartup', controller.start)
            assert controller.status()['identity'] == report['identity']
            report['status'] = 'passed'
            return
        root = timed("reset", lambda: controller.reset(41))
        if args.frame_capture_only:
            report['frameCapture'] = profile_frame_capture(runtime, args.output)
            report['status'] = 'passed'
            return
        root_snapshot = controller.snapshot_create()
        card = next(card for card in root["players"][1]["hand"] if card["cost"] * 10000 <= root["players"][1]["elixir"])
        played = controller.play_card(1, card["slot"], 4500, 24000)
        assert played["accepted"]
        child = controller.step(9)
        child_snapshot = controller.snapshot_create()
        for index in range(6):
            handle, expected = (root_snapshot, root) if index % 2 == 0 else (child_snapshot, child)
            started = perf_counter()
            state = controller.replay(41, [], expected["tick"], expected["digest"], handle["handle"], expected["tick"], expected["digest"])
            assert state["digest"] == expected["digest"]
            report["switchesMs"].append(round((perf_counter() - started) * 1000, 2))
            if index < 3:
                started = perf_counter()
                picture = runtime.frame_png()
                assert picture.startswith(b"\x89PNG\r\n\x1a\n")
                report["screenshotsMs"].append(round((perf_counter() - started) * 1000, 2))
                args.output.with_suffix(".png").write_bytes(picture)
        timed("coldReplay", lambda: controller.replay(41, [played["action"]], child["tick"], child["digest"]))
        report['routeCheckpoints'] = verify_checkpoint_cache(controller, args.output)
        print('routeCheckpoints: ' + json.dumps(report['routeCheckpoints']), flush=True)
        controller.close()
        timed("warmStartup", controller.start)
        reopened = timed("restartReplay", lambda: controller.replay(41, [played["action"]], child["tick"], child["digest"]))
        assert reopened['digest'] == child['digest']
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        install_runtime.Device.shell = original_shell
        bootstrap_runtime.bootstrap = original_bootstrap
        if not args.keep_device:
            runtime.process = device_process or runtime.process
        controller.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
