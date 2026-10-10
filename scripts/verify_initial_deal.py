"""Verify exact 4+4 native deals and restored histories on an isolated SDK device."""
import json
from pathlib import Path
from bridge.controller import BattleController
from bridge.runtime import AndroidRuntime
from bridge.engine_adapter import _load_package,engine_port
from bridge.match_config import STANDARD_DECK


def verify():
    runtime=AndroidRuntime()
    _load_package()
    from native_engine.engine import Engine
    controller=BattleController(runtime,engine_factory=lambda:Engine(port=engine_port()))
    report={'status':'running','checks':[]}
    try:
        controller.start()
        for seed in (41,1784463263,789123):
            first=list(STANDARD_DECK);second=list(reversed(first))
            root=controller.reset(seed,first,second,level=16,deal_mode='ordered')
            for owner,expected in enumerate((first,second)):
                player=root['players'][owner]
                assert [card['card'] for card in player['hand']]+player['cycle']==expected
            snapshot=controller.snapshot_create()
            receipt=controller.play_card(1,0,4500,22500)
            assert receipt['accepted']
            end=controller.step(5)
            actions=[receipt['action']]
            controller.replay(seed,actions,end['tick'],end['digest'],snapshot['handle'],90,root['digest'],first,second,level=16,deal_mode='ordered')
            controller.replay(seed,actions,end['tick'],end['digest'],deck0=first,deck1=second,level=16,deal_mode='ordered')
            report['checks'].append({'seed':seed,'kind':'exact-order-snapshot-cold-replay'})
        for omitted in (28000006,27000007):
            for seed in (41,):
                deck=list(STANDARD_DECK);deck[-1]=omitted
                state=controller.reset(seed,deck,list(STANDARD_DECK),level=16,deal_mode='ordered')
                player=state['players'][0]
                assert [card['card'] for card in player['hand']]+player['cycle']==deck
                report['checks'].append({'card':omitted,'seed':seed,'kind':'omitted-card-native-tail'})
        for tail in ((28000006,27000007),(27000007,28000006)):
            deck=list(STANDARD_DECK);deck[5],deck[7]=tail
            state=controller.reset(41,deck,list(STANDARD_DECK),level=16,deal_mode='ordered')
            player=state['players'][0]
            assert [card['card'] for card in player['hand']]+player['cycle']==deck
            report['checks'].append({'tail':tail,'kind':'two-omitted-card-native-tail'})
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=str(error));raise
    finally:
        controller.close()
        path=Path('.local-runtime/initial-deal-check.json');path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':print(json.dumps(verify()))
