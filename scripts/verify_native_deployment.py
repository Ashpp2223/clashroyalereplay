"""Reproduce card deployment through the packaged bridge, without UI input.

Uses an existing replay as read-only input and an isolated runtime/report folder.
The selected engine device must be idle: no other desktop bridge may be running.
"""
from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--read-diagnostics', action='store_true', help='Read persisted local failures without connecting to or changing the running app')
    parser.add_argument('--card', type=int)
    parser.add_argument('--owner', type=int, choices=(0, 1))
    parser.add_argument('--x', type=int)
    parser.add_argument('--y', type=int)
    parser.add_argument('--restore-only', action='store_true', help='Restore and verify the saved root without deploying a card')
    parser.add_argument('--runtime', type=Path, help='Legacy runtime configuration to copy into the isolated test folder')
    parser.add_argument('--modes', nargs='+', choices=('paused', 'playing'), default=['paused', 'playing'])
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--idle-seconds', type=float, default=0, help='Wait while polling cached desktop status before deployment, to verify paused connection liveness')
    parser.add_argument('--reconnect-check', action='store_true', help='Verify explicit reconnect clears a suspension without changing the battle')
    parser.add_argument('--speed-check', action='store_true', help='Verify all playback speeds while paused and while playing, including stale cached observations')
    parser.add_argument('--edge-check', action='store_true', help='Record isolated sibling and consecutive intervals; verify bounded playback, pause/resume, speed and exact endpoints')
    parser.add_argument('--bridge', type=Path, default=Path('release/native-desktop/native-bridge/native-bridge.exe'))
    parser.add_argument('--research-root', type=Path, default=Path('../clashroyale1016'))
    parser.add_argument('--output', type=Path, default=Path('.local-runtime/deployment-check'))
    args = parser.parse_args()
    if args.read_diagnostics:
        runtime = args.runtime or Path(os.environ['LOCALAPPDATA']) / 'local.clash-replay-tree/runtime'
        records = {name: json.loads((runtime / name).read_text(encoding='utf-8')) if (runtime / name).exists() else None
                   for name in ('last-failure.json', 'last-client-failure.json')}
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    if args.project is None:
        parser.error('--project is required unless --read-diagnostics is used')
    if not args.restore_only and any(value is None for value in (args.card, args.owner, args.x, args.y)):
        parser.error('Deployment requires --card, --owner, --x and --y; use --restore-only to verify an archive')
    if (args.x is not None and not 0 <= args.x <= 18000) or (args.y is not None and not 0 <= args.y <= 32000) or not 1 <= args.repeat <= 20 or not 0 <= args.idle_seconds <= 120:
        parser.error('Coordinates must be in the arena; repeat must be 1..20; idle-seconds must be 0..120')
    project = json.loads(args.project.read_text(encoding='utf-8-sig'))
    if project.get('schema') != 'cr-native-replay.v2':
        parser.error('A native replay v2 project is required')
    protocol = project['engine']['protocolVersion']
    if protocol not in ('cr-camp-control.v1', 'native-bridge.v1'):
        parser.error('Unknown engine protocol')
    profile = 'cn-38.4.5' if protocol == 'cr-camp-control.v1' else 'legacy'
    if not args.restore_only and args.card not in project['match'][f'deck{args.owner}'][:4]:
        parser.error('Choose a card in the selected starting hand')
    # The native control service accepts one persistent client. Do not compete
    # with the user's desktop session or terminate it to make this test run.
    running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq native-bridge.exe', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, check=True)
    if any(row and row[0].lower() == 'native-bridge.exe' for row in csv.reader(io.StringIO(running.stdout))):
        parser.error('A desktop bridge is running; close that session before this isolated test')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime_home = output / 'runtime'
    runtime_home.mkdir(exist_ok=True)
    if profile == 'legacy':
        runtime_source = args.runtime or Path(os.environ['LOCALAPPDATA']) / 'local.clash-replay-tree/runtime'
        for name in ('runtime.json', 'prepared.json'):
            shutil.copyfile(runtime_source / name, runtime_home / name)
    token = secrets.token_hex(32)
    environment = {**os.environ, 'CR_ENGINE_PROFILE': profile, 'CR_ENGINE_PORT': '26791' if profile != 'legacy' else '26789',
                   'CR_CAMP_RESEARCH_ROOT': str(args.research_root.resolve()),
                   'CR_RUNTIME_HOME': str(runtime_home), 'CR_BRIDGE_TOKEN': token,
                   'PYTHONIOENCODING': 'utf-8'}
    process = subprocess.Popen([str(args.bridge.resolve()), '--parent-stdin'], env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    report = {'status': 'running', 'profile': profile, 'card': args.card, 'owner': args.owner,
              'position': [args.x, args.y], 'bridgeSha256':hashlib.sha256(args.bridge.read_bytes()).hexdigest(),
              'cases': [], 'requests': []}
    base = None

    def request(path, body=None, *, frame=False):
        started = time.perf_counter()
        entry = {'path': path}
        try:
            headers = {'Authorization': 'Bearer ' + token}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            query = Request(base + path, headers=headers,
                            data=json.dumps(body).encode() if body is not None else None)
            with urlopen(query, timeout=8 if body is None else 120) as response:
                payload = response.read()
                entry['status'] = response.status
                if frame:
                    entry['bytes'] = len(payload)
                    assert payload, 'Empty frame'
                    return payload
                return json.loads(payload)
        except HTTPError as error:
            entry['status'] = error.code
            entry['error'] = error.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'{path}: {entry["error"]}') from error
        except Exception as error:
            entry['error'] = str(error)
            raise
        finally:
            entry['ms'] = round((time.perf_counter() - started) * 1000, 2)
            report['requests'].append(entry)

    try:
        greeting = json.loads(process.stdout.readline())
        assert greeting['protocol'] == 'native-bridge.v1'
        base = f'http://127.0.0.1:{greeting["port"]}'
        request('/v1/start', {})
        deadline = time.monotonic() + 180
        while True:
            status = request('/v1/status')
            if status.get('error'):
                raise RuntimeError(status['error'])
            if status['ready']:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Runtime did not become ready')
            time.sleep(.3)
        assert status['identity'] == project['engine'], 'Project and runtime identities differ'
        report['identity'] = status['identity']
        match = project['match']
        root = project['nodes'][project['rootNodeId']]
        config = {key: match[key] for key in ('seed', 'deck0', 'deck1', 'forms0', 'forms1', 'tower0', 'tower1', 'level', 'dealMode') if key in match}
        if args.restore_only:
            restored = request('/v1/replay', {**config, 'actions': [], 'targetTick': root['finalTick'], 'expectedDigest': root['stateDigest']})
            assert restored['digest'] == root['stateDigest']
            request('/v1/frame?fresh=1', frame=True)
            report['restore'] = {'tick': restored['tick'], 'digest': restored['digest'], 'status': 'passed'}
        for repeat in range(args.repeat):
            for mode in ([] if args.restore_only else args.modes):
                case = {'mode': mode, 'repeat': repeat + 1}
                report['cases'].append(case)
                before = request('/v1/reset', config)
                assert before['digest'] == root['stateDigest'], 'Initial state differs from saved root'
                hand = before['players'][args.owner]['hand']
                card = next(card for card in hand if card['card'] == args.card)
                case.update(beforeTick=before['tick'], form=card['form'])
                request('/v1/frame?fresh=1', frame=True)
                if args.idle_seconds:
                    print(json.dumps({'stage':'waiting-before-deployment','seconds':args.idle_seconds}),flush=True)
                    idle_deadline=time.monotonic()+args.idle_seconds
                    while time.monotonic()<idle_deadline:
                        request('/v1/status')
                        time.sleep(min(.5,max(0,idle_deadline-time.monotonic())))
                    case['idleSeconds']=args.idle_seconds
                    assert request('/v1/observe')['digest']==before['digest'], 'Paused keepalive changed the saved battle'
                if mode == 'playing':
                    request('/v1/resume', {})
                    time.sleep(.4)
                action = {'owner': args.owner, 'slot': card['slot'], 'x': args.x, 'y': args.y}
                if mode == 'paused':
                    action['expectedTick'] = before['tick']
                receipt = request('/v1/play-card', action)
                case.update(accepted=receipt['accepted'], afterTick=receipt['observation']['tick'])
                if receipt['accepted']:
                    assert receipt['action']['nativeCardId'] == args.card
                    assert receipt['action']['nativeForm'] == card['form']
                    case['action'] = receipt['action']
                else:
                    assert 'action' not in receipt, 'Rejected deployment must not create an action'
                request('/v1/frame?fresh=1', frame=True)
                state = request('/v1/observe')
                assert state['digest'] == receipt['observation']['digest']
                runtime = state['players'][args.owner].get('runtime') or {}
                case['abilityCount'] = len(runtime.get('abilityRuntime') or [])
                assert request('/v1/status')['ready'], 'Connection lost after deployment'
                case['status'] = 'passed'
                print(json.dumps(case, ensure_ascii=False), flush=True)
        if args.edge_check:
            edges=[]
            baseline=request('/v1/observe') if args.restore_only else request('/v1/reset',config)
            root_snapshot=request('/v1/snapshot-create',{})
            def restore_edge_base():
                state=request('/v1/snapshot-restore',{'handle':root_snapshot['handle']})
                assert state['tick']==baseline['tick'] and state['digest']==baseline['digest']
                return state
            for label,x,length in (('AB',3500,40),('AC',14500,60)):
                before=restore_edge_base()
                card=before['players'][1]['hand'][0]
                receipt=request('/v1/play-card',{'owner':1,'slot':card['slot'],'x':x,'y':25500,'expectedTick':before['tick']})
                assert receipt['accepted'], 'Isolated interval recording was rejected'
                endpoint=request('/v1/step',{'ticks':length-1})
                edges.append({'label':label,'base':before,'endpoint':endpoint,'actions':[receipt['action']]})
                if label=='AC':
                    # No reset or restore between consecutive intervals: CF
                    # must start from C's confirmed digest, with no new action.
                    final=request('/v1/step',{'ticks':20})
                    edges[-1]['next']={'label':'CF','base':endpoint,'endpoint':final,'actions':[]}
            report['edgeChecks']=[]
            for index,edge in enumerate(edges):
                before=restore_edge_base()
                assert before['digest']==edge['base']['digest']
                request('/v1/speed',{'multiplier':1 if index==0 else 4})
                def start_from(state,interval=edge):
                    return request('/v1/playback',{'actions':[a for a in interval['actions'] if a['tick']>state['tick']],
                        'targetTick':interval['endpoint']['tick'],'expectedTick':state['tick'],
                        'baseDigest':state['digest'],'expectedDigest':interval['endpoint']['digest']})
                assert start_from(before)['playing']
                case={'edge':edge['label'],'ticks':[]};report['edgeChecks'].append(case)
                if index==0:
                    time.sleep(.35)
                    paused=request('/v1/pause',{})
                    assert paused['tick']<edge['endpoint']['tick']
                    time.sleep(.2)
                    assert request('/v1/observe')['digest']==paused['digest']
                    request('/v1/speed',{'multiplier':2})
                    assert start_from(paused)['playing']
                    case['pauseResume']='passed'
                deadline=time.monotonic()+30
                while True:
                    status=request('/v1/status')
                    assert not status['error'],status['error']
                    state=request('/v1/observe')
                    case['ticks'].append(state['tick'])
                    assert state['tick']<=edge['endpoint']['tick'],'Edge playback overshot its node'
                    if not status['playing']:
                        assert status['playback']['completed']
                        break
                    assert time.monotonic()<deadline,'Edge playback stalled'
                    time.sleep(.05)
                assert state['tick']==edge['endpoint']['tick'] and state['digest']==edge['endpoint']['digest']
                time.sleep(.2)
                assert request('/v1/observe')['digest']==state['digest'],'Playback continued beyond the chosen edge'
                request('/v1/frame?fresh=1',frame=True)
                case.update(status='passed',endTick=state['tick'],digest=state['digest'])
                if edge.get('next'):
                    continuation=edge['next']
                    assert start_from(state,continuation)['playing']
                    deadline=time.monotonic()+30
                    while True:
                        status=request('/v1/status')
                        assert not status['error'],status['error']
                        state=request('/v1/observe')
                        assert state['tick']<=continuation['endpoint']['tick']
                        if not status['playing']:
                            assert status['playback']['completed']
                            break
                        assert time.monotonic()<deadline,'Consecutive interval stalled'
                        time.sleep(.05)
                    assert state['tick']==continuation['endpoint']['tick'] and state['digest']==continuation['endpoint']['digest']
                    time.sleep(.2)
                    assert request('/v1/observe')['digest']==state['digest']
                    request('/v1/frame?fresh=1',frame=True)
                    case['continuation']={'edge':'CF','status':'passed','endTick':state['tick'],'digest':state['digest']}
                print(json.dumps(case),flush=True)
            request('/v1/snapshot-release',{'handle':root_snapshot['handle']})
        if args.speed_check:
            checks = report['speedChecks'] = []
            for mode in ('paused', 'playing'):
                initial = request('/v1/reset', config)
                if mode == 'playing':
                    request('/v1/resume', {})
                advances = {}
                for multiplier in (.25, .5, 2, 4, 1):
                    # Desktop status is cached; deliberately let the native
                    # clock advance without refreshing its client's tick.
                    time.sleep(.3 if mode == 'playing' else .02)
                    case = {'mode':mode, 'multiplier':multiplier}
                    checks.append(case)
                    result = request('/v1/speed', {'multiplier':multiplier})
                    assert result['speed'] == multiplier
                    status = request('/v1/status')
                    assert status['ready'] and not status['error']
                    assert status['playing'] == (mode == 'playing'), 'Speed change lost playback state'
                    before = request('/v1/observe')
                    if mode == 'playing':
                        time.sleep(.6)
                    after = request('/v1/observe')
                    if mode == 'paused':
                        assert after['digest'] == initial['digest'], 'Paused speed change advanced or changed the battle'
                    else:
                        advanced = after['tick'] - before['tick']
                        assert advanced > 0, 'Playback stopped after a speed change'
                        advances[multiplier] = advanced
                        case['advanced'] = advanced
                    case.update(status='passed',tick=after['tick'])
                    print(json.dumps(case), flush=True)
                if mode == 'playing':
                    assert advances[4] > advances[.25]*2, 'Native clock did not apply the requested speed'
                    paused = request('/v1/pause', {})
                    assert not request('/v1/status')['playing']
                    assert request('/v1/observe')['digest'] == paused['digest']
                request('/v1/frame?fresh=1', frame=True)
        if args.reconnect_check:
            before_reconnect = request('/v1/observe')
            request('/v1/suspend', {})
            assert request('/v1/status')['error'], 'Suspension was not reported'
            status = request('/v1/start', {})
            assert status['ready'] and not status['playing'] and not status['error']
            assert request('/v1/observe')['digest'] == before_reconnect['digest']
            report['reconnect'] = 'passed'
        report['diagnostics'] = request('/v1/diagnostics')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        if base:
            try:
                report['diagnostics'] = request('/v1/diagnostics')
            except Exception as diagnostic_error:
                report['diagnosticError'] = str(diagnostic_error)
        raise
    finally:
        # Never repeat an uncertain mutation or touch the user's archive.
        try:
            if process.poll() is None:
                process.communicate('shutdown\n', timeout=70)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'report': str(output / 'report.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
