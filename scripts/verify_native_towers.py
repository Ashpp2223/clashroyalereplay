"""Verify every supported Tower Troop in rendered native battles and replay.

Point CR_RUNTIME_HOME and CR_ENGINE_PORT at an isolated prepared Android device.
Results and fresh scene images are saved under .local-runtime/tower-check/.
"""
import json
from pathlib import Path
from bridge.controller import BattleController
from bridge.runtime import AndroidRuntime
from bridge.match_config import TOWER_CARDS


def verify():
    output=Path('.local-runtime/tower-check')
    output.mkdir(parents=True,exist_ok=True)
    report={'status':'running','mode':'native-rendered','level':16,'cases':[]}
    controller=BattleController(AndroidRuntime())
    try:
        controller.start()
        report['identity']=controller.status()['identity']
        for index,card in enumerate(TOWER_CARDS):
            enemy=TOWER_CARDS[(index+1)%len(TOWER_CARDS)]
            config={'level':16,'deal_mode':'ordered','tower0':enemy['id'],'tower1':card['id']}
            state=controller.reset(41,**config)
            assert state['tick']==90
            assert controller.towers==(enemy['id'],card['id'])
            initial=[entity for entity in state['entities'] if entity['card']==-1]
            assert len(initial)==6,initial
            for owner in (0,1):
                assert len([entity for entity in initial if entity['owner']==owner])==3
                assert all(entity['hp']==entity['maxHp'] and entity['hp']>0 for entity in initial if entity['owner']==owner)
            frame,kind=controller.runtime.frame(fresh=True)
            image=output/f"{card['id']}.{'png' if kind=='image/png' else 'jpg'}"
            image.write_bytes(frame)
            actions=[]
            for owner in (0,1):
                # Both players deploy a Knight near the bridge.
                player=state['players'][owner]
                hand_card=next(item for item in player['hand'] if item['card']==26000000)
                receipt=controller.play_card(owner,hand_card['slot'],3500,13500 if owner==0 else 18500)
                assert receipt['accepted'],receipt
                actions.append(receipt['action']);state=receipt['observation']
            controller.step(160)
            snapshot=controller.snapshot_create()
            base=controller.observe()
            expected=controller.step(80)
            # Compare the entire suffix, including subsequent tower attacks.
            replay=controller.replay(41,[],expected['tick'],expected['digest'],snapshot['handle'],
                base['tick'],base['digest'],**config)
            assert replay['digest']==expected['digest']
            full=controller.replay(41,actions,expected['tick'],expected['digest'],**config)
            assert full['digest']==expected['digest']
            case={'tower0':enemy['id'],'tower1':card['id'],'name':card['name'],
                'initialTowers':initial,'finalTick':expected['tick'],'digest':expected['digest'],
                'checks':['rendered-start','both-players-play','snapshot-suffix','full-action-replay'],
                'image':str(image)}
            report['cases'].append(case)
            print(json.dumps({'name':card['name'],'status':'passed'},ensure_ascii=False),flush=True)
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error']=str(error)
        raise
    finally:
        controller.close()
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    verify()
