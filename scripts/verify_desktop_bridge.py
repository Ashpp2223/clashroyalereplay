from pathlib import Path
import argparse,json,os,secrets,subprocess,time
from urllib.request import Request,urlopen
parser=argparse.ArgumentParser(description="Check the frozen bridge, video continuity, snapshots and parent shutdown.")
parser.add_argument('--steady-seconds',type=int,default=0)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]; token=secrets.token_hex(32)
exe=Path(os.environ.get('CR_FROZEN_BRIDGE',str(root/'src-tauri/resources/native-bridge/native-bridge.exe')))
p=subprocess.Popen([str(exe),'--parent-stdin'],cwd=str(root/'.local-runtime'),env={**os.environ,'CR_RUNTIME_HOME':os.environ.get('CR_CHECK_RUNTIME_HOME',str(root/'.local-runtime')),'CR_BRIDGE_TOKEN':token},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
r={'status':'running','checks':[]}
try:
    hello=json.loads(p.stdout.readline()); base='http://127.0.0.1:'+str(hello['port'])
    def req(path,body=None):
        q=Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urlopen(q,timeout=180) as resp:return json.load(resp)
    req('/v1/start',{}); deadline=time.monotonic()+180
    while True:
        s=req('/v1/status')
        if s['error']: raise RuntimeError(s['error'])
        if s['ready']: break
        if time.monotonic()>deadline:raise TimeoutError('runtime startup')
        time.sleep(.3)
    state=req('/v1/reset',{'seed':41}); assert state['tick']==90
    r['checks'].append('frozen-bridge-native-tick-90')
    req('/v1/resume',{}); time.sleep(7)
    s=req('/v1/status'); assert not s['playing'] and s['error']; r['checks'].append('disconnected-desktop-automatically-paused')
    req('/v1/start',{}); req('/v1/pause',{})
    if args.steady_seconds:
        state=req('/v1/replay',{'seed':41,'actions':[],'targetTick':100})
        snapshot=req('/v1/snapshot-create',{})
        deadline=time.monotonic()+args.steady_seconds
        formats={}; frames=0; restores=0
        while time.monotonic()<deadline:
            req('/v1/status')
            q=Request(base+'/v1/frame',headers={'Authorization':'Bearer '+token})
            with urlopen(q,timeout=12) as response:
                data=response.read(); kind=response.headers.get_content_type()
            assert data.startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8'))
            formats[kind]=formats.get(kind,0)+1; frames+=1
            if frames%10==0:
                req('/v1/step',{'ticks':1})
                restored=req('/v1/snapshot-restore',{'handle':snapshot['handle']})
                assert restored['digest']==state['digest']; restores+=1
            time.sleep(.4)
        req('/v1/snapshot-release',{'handle':snapshot['handle']})
        r['steady']={'seconds':args.steady_seconds,'frames':frames,'formats':formats,'snapshotRestores':restores}
        r['steady']['video']=req('/v1/diagnostics')['video']
        if formats.get('image/jpeg',0)<10:
            raise AssertionError('Continuous video did not become ready; screenshot fallback alone is insufficient')
        if args.steady_seconds>=180 and r['steady']['video']['sessionsStarted']<2:
            raise AssertionError('Screenrecord restart was not observed')
        r['checks'].extend(['video-continuity-over-screenrecord-restart' if args.steady_seconds>=180 else 'continuous-native-video','repeated-live-snapshot-restoration'])
    r['status']='passed'
except Exception as ex:r.update(status='failed',error=str(ex))
finally:
    p.stdin.close()
    try:p.wait(timeout=20)
    except subprocess.TimeoutExpired:p.kill();p.wait();r.update(status='failed',shutdown='forced')
    err=p.stderr.read().decode('utf-8','replace')
    if err:r['stderr']=err[-2000:]
    r['exitCode']=p.returncode
    if p.returncode:r['status']='failed'
    (root/'.local-runtime/frozen-bridge-check.json').write_text(json.dumps(r,indent=2),encoding='utf-8');print(json.dumps(r))
    raise SystemExit(0 if r['status']=='passed' else 1)
