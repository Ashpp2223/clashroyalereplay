"""Verify each pinned card and form by consuming it in a real native battle.

Run with CR_RUNTIME_HOME and CR_ENGINE_PORT pointing to an isolated prepared
Android SDK device. The fixture uses the desktop default: level-16 cards and towers.
Native battle rules and evolution cycles remain unchanged.
"""
import json
from pathlib import Path
from bridge.runtime import AndroidRuntime
from bridge.engine_adapter import _load_package,engine_port
from bridge.match_config import CARDS,STANDARD_DECK
from bridge.controller import BattleController
_load_package()
from native_engine.engine import Engine,Play,match_configuration
def verify_target_ability(engine):
 """Mega Minion's teleport requires a visible opponent; an empty lane is insufficient."""
 deck=[26000039,26000003,26000010,26000030,26000031,26000084,28000016,28000008]
 state=engine.reset(41,deck,deck,[0]*8,[2]+[0]*7,level=16)
 for attempt in range(50):
  state=engine.step(ticks=20).state;player=state.players[1]
  target=next((card for card in player.hand if card.card==26000039),None)
  if not target:
   card=min(player.hand,key=lambda card:card.cost)
   if card.cost*10000<=player.elixir:engine.step([Play(1,card.slot,4500,26000)],ticks=1)
   continue
  if player.elixir<50000:continue
  red=state.players[0];enemy=next((card for card in red.hand if card.card==26000003),None)
  if not enemy or enemy.cost*10000>red.elixir:
   card=min(red.hand,key=lambda card:card.cost)
   if card.cost*10000<=red.elixir:engine.step([Play(0,card.slot,4500,14000)],ticks=1)
   continue
  engine.step([Play(0,enemy.slot,4500,14000)],ticks=1)
  engine.step([Play(1,target.slot,4500,18000)],ticks=1)
  for wait in range(120):
   state=engine.step(ticks=2).state
   abilities=(state.players[1].runtime or {}).get('abilityRuntime') or []
   ready=next((ability for ability in abilities if ability['available'] and not ability['remainingCooldownMs'] and ability['championEntityKeys']),None)
   if not ready:continue
   key=next(key for key in ready['championEntityKeys'] if key);before=state
   ack=engine.request(f"activate-ability 1 -2 {key['secondaryIndex']} 1");state=engine.step(ticks=1).state
   if not ack.get('ok') or not BattleController._ability_changed(before,state,1,key['secondaryIndex'],ready['actionDataGlobalId']):raise AssertionError('targeted ability was not consumed')
   return {'id':26000039,'mode':2,'name':ready['actionDataName'],'tick':state.tick,'fixture':'visible enemy giant'}
  break
 raise AssertionError('Mega Minion targeted ability was not ready')

runtime=AndroidRuntime();engine=None
report={'schema':'cr-card-form-check.v1','fixtureLevel':16,'status':'running','base':[],'forms':[],'failed':[],'abilityChecks':[],'abilityPending':[]}
try:
 runtime.start();engine=Engine(port=engine_port())
 from bridge.runtime import PROBE_SHA256,MANIFEST,UPSTREAM_COMMIT
 report['identity']={'upstreamCommit':UPSTREAM_COMMIT,'probeSha256':PROBE_SHA256,'apkSha256':runtime.apk_sha256,'libgSha256':MANIFEST['libg_sha256'],'runtimeFingerprintSha256':runtime.runtime_fingerprint_sha256}
 cases=[(entry,0) for entry in CARDS]+[(entry,mode) for entry in CARDS for mode in (1,2) if entry['forms']&mode]
 for index,(entry,mode) in enumerate(cases):
  target=entry['id'];deck=[target]+[card for card in (26000010,26000030,26000031,26000084,28000016,28000008,28000017,26000019) if card!=target][:7];forms=[mode]+[0]*7
  try:
   match=match_configuration(41,deck,deck,[0]*8,forms,level=16)
   engine.request('configure '+json.dumps(match,separators=(',',':')))
   engine.request('step 90');engine.state=engine._capture();state=engine.state
   casts=0;previous_casts=0;ability_checked=False
   for attempt in range(65):
    if state.finalized:raise AssertionError('fixture reached terminal before target form')
    state=engine.step(ticks=100).state
    player=state.players[1];target_card=next((c for c in player.hand if c.card==target),None)
    card=target_card or min(player.hand,key=lambda c:c.cost)
    if card.cost*10000>player.elixir:continue
    if target==28000006 and previous_casts==0 and target_card:
     card=next(c for c in player.hand if c.card!=target)
    transition=engine.step([Play(1,card.slot,4500,26000)],ticks=1);state=transition.state
    if not transition.executed[0]:continue
    previous_casts+=1
    if card.card!=target:continue
    casts+=1
    event=next(event for event in state.plays if event.tick==state.tick and event.owner==1 and event.card==target)
    if mode and event.form!=mode:continue
    result={'id':target,'name':entry['name'],'mode':mode,'tick':state.tick,'form':event.form,'cost':card.cost,'casts':casts}
    if mode==2 or entry['id'] in (26000065,26000069,26000072,26000074,26000077,26000093,26000099,26000103):
     for wait in range(12):
      state=engine.step(ticks=20).state
      abilities=(state.players[1].runtime or {}).get('abilityRuntime') or []
      usable=next((a for a in abilities if a['available'] and a['remainingCooldownMs']==0 and a['championEntityKeys']),None)
      if not usable:continue
      key=next(k for k in usable['championEntityKeys'] if k)
      before=state
      ack=engine.request(f"activate-ability 1 -2 {key['secondaryIndex']} 1")
      if not ack.get('ok'):raise AssertionError(ack)
      state=engine.step(ticks=1).state
      if not BattleController._ability_changed(before,state,1,key['secondaryIndex'],usable['actionDataGlobalId']):raise AssertionError('ability state did not confirm execution')
      result['ability']={'id':usable['actionDataGlobalId'],'name':usable['actionDataName'],'tick':state.tick};ability_checked=True;break
     if not ability_checked:report['abilityPending'].append({'id':target,'mode':mode,'reason':'no ready ability observed in quiet fixture'})
     else:report['abilityChecks'].append({'id':target,'mode':mode,**result['ability']})
    report['forms' if mode else 'base'].append(result);break
   else:raise AssertionError('requested form was never consumed')
  except Exception as error:report['failed'].append({'id':target,'name':entry['name'],'mode':mode,'error':str(error),'log':runtime.command('logcat','-d','-s','CRNativeProbe:E','-t','8',check=False).stdout[-1200:]})
  if (index+1)%20==0 or index+1==len(cases):print(json.dumps({'tested':index+1,'total':len(cases),'base':len(report['base']),'forms':len(report['forms']),'failed':report['failed'][-1:]},ensure_ascii=False),flush=True)
  Path('.local-runtime/all-card-form-matrix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 if any(entry['id']==26000039 for entry in report['abilityPending']):
  report['abilityChecks'].append(verify_target_ability(engine))
  report['abilityPending']=[entry for entry in report['abilityPending'] if entry['id']!=26000039]
 report['status']='passed' if not report['failed'] and not report['abilityPending'] else 'failed'
finally:
 if engine:engine.close()
 runtime.close();Path('.local-runtime/all-card-form-matrix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'base':len(report['base']),'forms':len(report['forms']),'failed':report['failed']},ensure_ascii=False))

raise SystemExit(0 if report['status']=='passed' else 1)
