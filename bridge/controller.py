"""Serial, confirmed battle operations and deterministic route replay."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from .engine_adapter import new_engine, new_play
from .runtime import AndroidRuntime, MANIFEST, PROBE_SHA256, UPSTREAM_COMMIT
from .match_config import validate_decks, validate_forms, validate_level, validate_deal_mode, validate_towers, DEFAULT_TOWER, ordered_deal, remap_deal, STANDARD_DECK


def observation(state: Any) -> dict:
    players = [
        {"owner": player.owner, "elixir": player.elixir,
         "hand": [{"slot": card.slot, "card": card.card, "cost": card.cost}
                  for card in player.hand]}
        for player in state.players
    ]
    facts = {
        "tick": state.tick, "finalized": state.finalized, "result": state.result,
        "crowns": list(state.crowns), "players": players,
        "entities": [
            {"id": entity.id, "owner": entity.owner, "card": entity.card,
             "x": entity.x, "y": entity.y, "hp": entity.hp, "maxHp": entity.max_hp}
            for entity in state.entities
        ],
    }
    for player,native_player in zip(players,state.players):
        runtime=getattr(native_player,'runtime',None) or {}
        if runtime.get('abilityRuntime') or any(slot.get('progress',0)>0 for slot in runtime.get('evolutionRuntime') or []):
            player['runtime']=runtime
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    enriched = [{**player, "hand":[{**card,"form":getattr(native_card,'parameter',0)&0x0f}
                                  for card,native_card in zip(player['hand'],native_player.hand)],
                 "cycle":list(native_player.cycle), "runtime": getattr(native_player, 'runtime', None)}
                for player,native_player in zip(players,state.players)]
    result = {**facts, "players": enriched, "digest": digest}
    if getattr(state,'crop_profile',None): result['cropProfile']=state.crop_profile
    return result


class BattleController:
    def __init__(self, runtime: AndroidRuntime | None = None, engine_factory=new_engine, *, replay_speed: int = 4):
        if replay_speed not in (1, 4):
            raise ValueError("重放速度必须为 1 或 4")
        self.runtime = runtime
        self.engine_factory = engine_factory
        self.engine = None
        self.lock = threading.RLock()
        self.playing = False
        self.playback_speed = 1
        self.replay_speed = replay_speed
        self.seed: int | None = None
        self.decks = (STANDARD_DECK, STANDARD_DECK)
        self.forms = ((0,)*8,(0,)*8)
        self.level = None
        self.deal_mode = None
        self.towers = (DEFAULT_TOWER, DEFAULT_TOWER)
        self.attestation: dict | None = None
        self.last_error: str | None = None
        self._snapshot_handles: set[int] = set()
        self.playback_segment: dict | None = None

    def status(self) -> dict:
        # Metadata reads must remain available during a long serialized replay.
        segment = self.playback_segment
        result = {
            "stage": self.runtime.stage if self.runtime else "测试环境",
            "stageCode": getattr(self.runtime,"stage_code",None) if self.runtime else "ready",
            "ready": self.engine is not None,
            "playing": self.playing,
            "playback": None if segment is None else {
                "targetTick": segment['targetTick'],
                "completed": segment['completed'],
            },
            "error": self.last_error,
            "identity": {
                "upstreamCommit": UPSTREAM_COMMIT,
                "probeSha256": PROBE_SHA256,
                "apkSha256": self.runtime.apk_sha256 if self.runtime else MANIFEST["apk_sha256"],
                "libgSha256": MANIFEST["libg_sha256"],
                "runtimeFingerprintSha256": self.runtime.runtime_fingerprint_sha256 if self.runtime else MANIFEST["runtime_fingerprint_sha256"],
                "protocolVersion": "native-bridge.v1",
                "cropProfileId": "stock-v15-1080x1920",
            },
            "cropProfile": {
                "id": "stock-v15-1080x1920",
                "nativeWidth": 1080, "nativeHeight": 1920,
                "enemyHud": [0, 0, 1080, 282],
                "arena": [54, 282, 972, 1384],
                "myHud": [0, 1666, 1080, 254],
                "world": [111, 436, 856, 1230],
                "handCentersX": [211, 354, 497, 640],
                "enemyHandReversed": True,
            },
            "capabilities": self.attestation.get("capabilities") if self.attestation else None,
        }
        profile = getattr(self.runtime, 'profile', None)
        if profile:
            result.update(identity=profile['identity'], cropProfile=profile['cropProfile'],
                          engineCatalog=profile['catalog'], engineProfile=profile['id'])
            if self.engine and getattr(self.engine.state,'crop_profile',None):
                result['cropProfile']=self.engine.state.crop_profile
        return result

    def start(self) -> dict:
        with self.lock:
            if self.engine is not None:
                self.last_error = None
                return self.status()
        if self.runtime is None:
            raise RuntimeError("无 Android 运行时")
        self.last_error = None
        try:
            attestation = self.runtime.start()
            with self.lock:
                self.attestation = attestation
                self.engine = self.engine_factory()
                self.last_error = None
                return self.status()
        except Exception as error:
            self.runtime.close()
            with self.lock:
                self.last_error = str(error)
                self.runtime.stage = "启动失败"
                self.runtime.stage_code = "failed"
            raise

    def suspend(self, reason: str) -> dict:
        with self.lock:
            if self.playing and self.engine:
                try:
                    self.pause()
                except Exception:
                    try:
                        self.close()
                    except Exception:
                        pass
            self.playing = False
            self.last_error = reason
            return {"playing": False, "error": reason}

    def reset(self, seed: int, deck0=None, deck1=None, forms0=None, forms1=None, level=None, deal_mode=None, tower0=None, tower1=None) -> dict:
        with self.lock:
            if not isinstance(seed, int) or seed < 0 or seed > 2**31 - 1:
                raise ValueError("随机种子无效")
            decks = validate_decks(deck0, deck1)
            forms = validate_forms(decks,forms0,forms1)
            level = validate_level(level)
            deal_mode = validate_deal_mode(decks,deal_mode)
            towers = validate_towers(tower0,tower1)
            engine = self._engine()
            self.playback_segment = None
            configured_decks,configured_forms=decks,forms
            seen=set()
            for attempt in range(8 if deal_mode else 1):
                if configured_decks in seen:
                    raise RuntimeError('原生引擎无法采用这个初始牌序，请调整待抽顺序')
                seen.add(configured_decks)
                tower_options = {} if towers == (DEFAULT_TOWER,DEFAULT_TOWER) else {'tower0':towers[0],'tower1':towers[1]}
                state = (engine.reset(seed=seed, warmup_speed=self.replay_speed) if configured_decks == (STANDARD_DECK, STANDARD_DECK) and not any(any(side) for side in configured_forms) and level is None and not tower_options
                         else engine.reset(seed=seed, deck0=list(configured_decks[0]), deck1=list(configured_decks[1]), forms0=list(configured_forms[0]), forms1=list(configured_forms[1]), level=level, warmup_speed=self.replay_speed, **tower_options))
                if deal_mode is None:
                    break
                actual = ordered_deal(state)
                matches_deal = getattr(engine, "matches_deal", None)
                if actual == decks or (matches_deal and matches_deal(actual, decks)):
                    break
                configured_decks,configured_forms=remap_deal(configured_decks,configured_forms,actual,decks)
            else:
                raise RuntimeError('初始牌序未通过原生引擎校验，请调整待抽顺序')
            self.seed = seed
            self.decks = decks
            self.forms = forms
            self.level = level
            self.deal_mode = deal_mode
            self.towers = towers
            self.playing = False
            if self.playback_speed != 1:
                self._set_engine_speed(self.playback_speed)
            return observation(state)

    def observe(self) -> dict:
        with self.lock:
            engine = self._engine()
            state = engine._capture() if self.playing else engine.state
            if state is None:
                raise RuntimeError("尚未开始战斗")
            return observation(state)

    def keepalive(self) -> bool:
        # CN control closes an idle socket after 20 seconds. Desktop status and
        # paused observations are cached, so they do not renew that connection.
        # Use its read-only status command on the SAME serialized connection.
        if not self.lock.acquire(blocking=False):
            return False
        try:
            native = getattr(self.engine, 'native', None)
            if self.playing or native is None or getattr(native, 'connection', None) is None:
                return False
            try:
                native.status()
            except Exception as error:
                self.engine.state = None
                self.last_error = f'原生战斗连接中断：{error}'
                raise
            return True
        finally:
            self.lock.release()

    def pause(self) -> dict:
        with self.lock:
            state = self._engine().pause()
            self.playing = False
            return observation(state)

    def resume(self, *, segment: bool = False) -> dict:
        with self.lock:
            if segment:
                route = self.playback_segment
                if route is None or route['completed']:
                    raise ValueError('请先选择一个尚未播完的时间段')
                route.update(anchorTick=self._engine().state.tick, anchorTime=time.monotonic())
            else:
                self.playback_segment = None
                self._engine().resume()
            self.playing = True
            self.last_error = None
            return {"playing": True}

    def speed(self, multiplier: float) -> dict:
        with self.lock:
            if multiplier not in (0.25, 0.5, 1, 2, 4):
                raise ValueError("不支持的播放速度")
            # Native speed commands require an exact generation/tick. During
            # playback a cached observation is already stale; freeze and read
            # the live tick before changing speed, then restore playback once.
            was_playing = self.playing
            was_segment = self.playback_segment is not None
            if was_playing:
                self.pause()
            self._set_engine_speed(multiplier)
            self.playback_speed = multiplier
            if was_playing:
                self.resume(segment=was_segment)
            return {"speed": multiplier}

    def start_playback(self, actions: list[dict], target_tick: int, expected_tick: int,
                       base_digest: str, expected_digest: str) -> dict:
        """Play one saved edge. Never select or execute a following edge."""
        with self.lock:
            if self.playing:
                self.pause()
            before = self.observe()
            if (type(expected_tick) is not int or before['tick'] != expected_tick or
                    not base_digest or before['digest'] != base_digest or
                    type(target_tick) is not int or target_tick < expected_tick or not expected_digest):
                raise ValueError('时间段基点已变化，请重新选择连线')
            if not isinstance(actions,list) or any(not isinstance(action,dict) or
                    action.get('kind') not in ('play-card','activate-ability') or
                    type(action.get('tick')) is not int or not expected_tick < action['tick'] <= target_tick or
                    type(action.get('sequence')) is not int for action in actions):
                raise ValueError('时间段动作日志无效')
            if actions != sorted(actions,key=lambda action:(action['tick'],action['sequence'])):
                raise ValueError('时间段动作顺序无效')
            self.playback_segment = {'actions':actions, 'targetTick':target_tick,
                'expectedDigest':expected_digest, 'completed':False}
            if target_tick == expected_tick:
                if before['digest'] != expected_digest:
                    self.playback_segment = None
                    raise RuntimeError('时间段终点摘要不一致')
                self.playback_segment['completed'] = True
                self.playing = False
            else:
                self.resume(segment=True)
            return {**before, 'playing':self.playing}

    def advance_playback(self, now: float | None = None) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        try:
            route = self.playback_segment
            if not self.playing or route is None:
                return False
            now = time.monotonic() if now is None else now
            current = self._engine().state.tick
            target = min(route['targetTick'], current+8,
                         route['anchorTick']+int(max(0,now-route['anchorTime'])*20*self.playback_speed))
            if target <= current:
                return False
            actions = [action for action in route['actions'] if current < action['tick'] <= target]
            try:
                self._replay_actions(actions,target)
                if target == route['targetTick']:
                    state = self.observe()
                    if state['digest'] != route['expectedDigest']:
                        raise RuntimeError('时间段终点摘要不一致，请恢复节点')
                    route['completed'] = True
                    self.playing = False
            except Exception as error:
                self.playing = False
                self.playback_segment = None
                self.last_error = str(error)
                raise
            return True
        finally:
            self.lock.release()

    def _set_engine_speed(self, multiplier: float) -> None:
        result = self._engine().set_speed(multiplier)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "变速失败"))

    def step(self, ticks: int = 1, expected_tick: int | None = None) -> dict:
        with self.lock:
            if expected_tick is not None:
                if self.playing:
                    raise ValueError('请先暂停，再逐帧前进')
                if type(expected_tick) is not int or self._engine().state is None or self._engine().state.tick != expected_tick:
                    raise ValueError('单步基点已变化，请恢复当前局面')
            if self.playing:
                self.pause()
            if not isinstance(ticks, int) or not 1 <= ticks <= 1000000:
                raise ValueError("步进 tick 无效")
            self.playback_segment = None
            return self._step_paused(ticks)

    def _step_paused(self, ticks: int) -> dict:
        transition = self._engine().step(ticks=ticks)
        if transition.advanced != ticks:
            raise RuntimeError("实际推进 tick 与请求不符")
        return observation(transition.state)

    def play_card(self, owner: int, slot: int, x: int, y: int, expected_tick: int | None = None) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            self.playback_segment = None
            if owner not in (0, 1) or not isinstance(slot, int) or slot not in range(4):
                raise ValueError("玩家或手牌槽位无效")
            if not all(isinstance(value, int) for value in (x, y)) or not 0 <= x <= 18000 or not 0 <= y <= 32000:
                raise ValueError("落点坐标无效")
            engine = self._engine()
            before = engine.state
            if before is None:
                raise RuntimeError("战斗尚未暂停")
            if expected_tick is not None:
                if type(expected_tick) is not int:
                    raise ValueError('出牌基点无效')
                if expected_tick != before.tick:
                    return {'accepted':False,'observation':observation(before)}
            hand_card = next((card for card in before.players[owner].hand if card.slot == slot), None)
            if hand_card is None:
                raise ValueError("手牌槽位不可用")
            transition = engine.step([new_play(owner, slot, x, y)], ticks=1)
            after = observation(transition.state)
            if not transition.executed[0]:
                return {"accepted": False, "observation": after}
            event = next((event for event in transition.state.plays
                          if event.owner == owner and event.card == hand_card.card and
                          event.tick == before.tick + 1), None)
            if event is None:
                raise RuntimeError("探针确认结果与动作事件不一致")
            return {
                "accepted": True, "observation": after,
                "action": {"tick": event.tick, "sequence": event.sequence,
                           "owner": owner, "kind": "play-card", "nativeCardId": hand_card.card,
                           "slot": slot, "nativeX": x, "nativeY": y, "nativeForm":getattr(event,'form',0)},
            }

    def snapshot_create(self) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            result = self._engine().request("snapshot-create")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "创建快照失败"))
            self._snapshot_handles.add(result['handle'])
            return result

    @staticmethod
    def _ability(state, owner, entity_id, ability_id):
        runtime = getattr(state.players[owner], 'runtime', None) or {}
        return next((ability for ability in runtime.get('abilityRuntime') or []
                     if ability['actionDataGlobalId'] == ability_id and
                     any(key and key['secondaryIndex'] == entity_id for key in ability['championEntityKeys'])), None)

    @classmethod
    def _ability_changed(cls, before, after, owner, entity_id, ability_id):
        old = cls._ability(before,owner,entity_id,ability_id)
        current = cls._ability(after,owner,entity_id,ability_id)
        return bool(old and ((current is None and not any(entity.id==entity_id for entity in after.entities)) or
                            (current is not None and (current['remainingCooldownMs'] > old['remainingCooldownMs'] or
                            current['remainingChargesRaw'] < old['remainingChargesRaw'] or
                            current['buttonState'] != old['buttonState'])) or
                            after.players[owner].elixir < before.players[owner].elixir))

    def activate_ability(self, owner, entity_id, ability_id):
        with self.lock:
            if self.playing: self.pause()
            self.playback_segment = None
            if type(owner) is not int or owner not in (0,1) or type(entity_id) is not int or entity_id <= 0 or type(ability_id) is not int or ability_id <= 0:
                raise ValueError('技能来源无效')
            engine = self._engine(); before=engine.state
            ability = self._ability(before,owner,entity_id,ability_id) if before else None
            if not ability or not ability['available'] or ability['remainingCooldownMs']:
                raise ValueError('当前技能不可释放')
            accepted=engine.request(f'activate-ability {owner} -2 {entity_id} 1')
            if not accepted.get('ok'):
                return {'accepted':False,'observation':observation(before)}
            after=engine.step(ticks=1).state
            executed=self._ability_changed(before,after,owner,entity_id,ability_id)
            result={'accepted':executed,'observation':observation(after)}
            if executed:
                result['action']={'tick':after.tick,'sequence':before.next_sequence,'owner':owner,
                                  'kind':'activate-ability','nativeEntityId':entity_id,'nativeAbilityId':ability_id}
            return result

    def snapshot_restore(self, handle: int) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            self.playback_segment = None
            if not isinstance(handle, int) or handle <= 0:
                raise ValueError("快照句柄无效")
            engine = self._engine()
            result = engine.request(f"restore {handle}")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "恢复快照失败"))
            # The probe's pending replay schedule lives outside the native snapshot.
            # A different branch must not execute commands left by the previous one.
            cleared = engine.request("replay-schedule-clear")
            if not cleared.get("ok"):
                engine.state = None
                raise RuntimeError(cleared.get("error", "恢复后清理旧分支动作失败，请重新开局"))
            engine.state = engine._capture()
            return observation(engine.state)

    def snapshot_release(self, handle: int) -> dict:
        with self.lock:
            if not isinstance(handle, int) or handle <= 0:
                raise ValueError("快照句柄无效")
            result = self._engine().request(f"release-snapshot {handle}")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "释放快照失败"))
            self._snapshot_handles.discard(handle)
            return result

    def replay(self, seed: int, actions: list[dict], target_tick: int, expected_digest: str | None = None,
               snapshot_handle: int | None = None, base_tick: int | None = None,
               base_digest: str | None = None, deck0=None, deck1=None, forms0=None, forms1=None, level=None, deal_mode=None, tower0=None, tower1=None,
               *, checkpoints: list[dict] | None = None) -> dict:
        """Replay an event suffix from a live snapshot, or the full path from the seed."""
        with self.lock:
            decks = validate_decks(deck0, deck1)
            forms = validate_forms(decks,forms0,forms1)
            level = validate_level(level)
            deal_mode = validate_deal_mode(decks,deal_mode)
            towers = validate_towers(tower0,tower1)
            checkpoints = [] if checkpoints is None else checkpoints
            if not isinstance(checkpoints, list) or len(checkpoints) > 32:
                raise ValueError("重放缓存节点无效")
            seen = set()
            previous_tick = base_tick if snapshot_handle is not None and type(base_tick) is int else 0
            for checkpoint in checkpoints:
                if (not isinstance(checkpoint, dict) or not isinstance(checkpoint.get('nodeId'), str) or
                        not checkpoint['nodeId'] or checkpoint['nodeId'] in seen or
                        type(checkpoint.get('tick')) is not int or checkpoint['tick'] < previous_tick or
                        type(target_tick) is not int or checkpoint['tick'] > target_tick or
                        not isinstance(checkpoint.get('digest'), str) or not checkpoint['digest']):
                    raise ValueError("重放缓存节点无效")
                seen.add(checkpoint['nodeId'])
                previous_tick = checkpoint['tick']
            if snapshot_handle is None:
                state = self.reset(seed, list(decks[0]), list(decks[1]),list(forms[0]),list(forms[1]),level,deal_mode,*towers)
            else:
                if self.seed != seed or self.decks != decks or self.forms != forms or self.level != level or self.deal_mode != deal_mode or self.towers != towers or not isinstance(base_tick, int) or not base_digest:
                    raise ValueError("快照基点无效")
                state = self.snapshot_restore(snapshot_handle)
                if state["tick"] != base_tick or state["digest"] != base_digest:
                    raise RuntimeError("快照基点状态不一致")
            if (not isinstance(target_tick, int) or target_tick < state["tick"] or
                    any(checkpoint['tick'] < state['tick'] for checkpoint in checkpoints)):
                raise ValueError("目标 tick 早于重放基点")
            ordered = sorted(actions, key=lambda action: (action["tick"], action["sequence"]))
            if ordered != actions:
                raise ValueError("动作日志顺序无效")
            # Exact cached-node restores stay on the existing fast path. Long
            # suffixes use the stock renderer's 4x clock without dropping ticks.
            accelerate = target_tick - state["tick"] >= 20 and self.replay_speed != self.playback_speed
            if accelerate:
                self._set_engine_speed(self.replay_speed)
            snapshots = []
            try:
                try:
                    offset = 0
                    for checkpoint in checkpoints:
                        end = offset
                        while end < len(ordered) and ordered[end]['tick'] <= checkpoint['tick']:
                            end += 1
                        self._replay_actions(ordered[offset:end], checkpoint['tick'])
                        offset = end
                        current = self.observe()
                        if current['tick'] != checkpoint['tick'] or current['digest'] != checkpoint['digest']:
                            raise RuntimeError(f"重放状态摘要不一致：tick {checkpoint['tick']}")
                        # Snapshot allocation is optional; deterministic replay is
                        # still valid if the native snapshot store is full.
                        try:
                            receipt = self.snapshot_create()
                        except Exception:
                            continue
                        handle = receipt.get('handle')
                        if type(handle) is int and handle > 0:
                            if receipt.get('tick') == checkpoint['tick']:
                                snapshots.append({**checkpoint, 'handle': handle})
                            else:
                                self.snapshot_release(handle)
                    self._replay_actions(ordered[offset:], target_tick)
                    result = self.observe()
                    if expected_digest and result["digest"] != expected_digest:
                        raise RuntimeError(f"重放状态摘要不一致：tick {target_tick}")
                finally:
                    if accelerate:
                        self._set_engine_speed(self.playback_speed)
            except Exception:
                # Do not leak handles from a failed route or speed restoration.
                for snapshot in snapshots:
                    try:
                        self.snapshot_release(snapshot['handle'])
                    except Exception:
                        pass
                raise
            if checkpoints:
                result['snapshots'] = snapshots
            return result

    def _replay_actions(self, ordered: list[dict], target_tick: int) -> None:
        offset = 0
        while offset < len(ordered):
            tick = ordered[offset]["tick"]
            group = []
            while offset < len(ordered) and ordered[offset]["tick"] == tick:
                group.append(ordered[offset])
                offset += 1
            if any(action.get("kind") not in ("play-card", "activate-ability") for action in group) or tick > target_tick:
                raise ValueError("动作不受当前引擎支持或晚于目标 tick")
            current = self._engine().state.tick
            if tick <= current:
                raise ValueError("动作 tick 不可重放")
            gap = tick - current - 1
            if gap:
                self._step_paused(gap)
            before = self._engine().state
            cards = [action for action in group if action['kind']=='play-card']
            abilities = [action for action in group if action['kind']=='activate-ability']
            for action in cards:
                card = next((card for card in before.players[action["owner"]].hand
                             if card.slot == action["slot"]), None)
                if card is None or card.card != action["nativeCardId"]:
                    raise RuntimeError(f"重放手牌分歧：tick {tick}")
                if 'nativeForm' in action and card.parameter & 0x0f != action['nativeForm']:
                    raise RuntimeError(f'重放卡牌形态分歧：tick {tick}')
            for action in abilities:
                ability = self._ability(before,action['owner'],action['nativeEntityId'],action['nativeAbilityId'])
                if not ability or not ability['available']:
                    raise RuntimeError(f'重放技能不可用：tick {tick}')
                ack = self._engine().request(f"activate-ability {action['owner']} -2 {action['nativeEntityId']} 1")
                if not ack.get('ok'):raise RuntimeError(f'重放技能被拒绝：tick {tick}')
            transition = self._engine().step(
                [new_play(a["owner"], a["slot"], a["nativeX"], a["nativeY"]) for a in cards], ticks=1)
            if transition.advanced != 1 or not all(transition.executed):
                raise RuntimeError(f"重放动作被拒绝：tick {tick}")
            actual = [(event.owner, event.card) for event in transition.state.plays
                      if event.tick == tick]
            expected = [(a["owner"], a["nativeCardId"]) for a in cards]
            if actual != expected:
                raise RuntimeError(f"重放事件分歧：tick {tick}")
            if any(not self._ability_changed(before,transition.state,a['owner'],a['nativeEntityId'],a['nativeAbilityId']) for a in abilities):
                raise RuntimeError(f'重放技能状态未确认：tick {tick}')
        remaining = target_tick - self._engine().state.tick
        if remaining:
            self._step_paused(remaining)

    def _engine(self):
        if self.engine is None:
            raise RuntimeError("原生引擎尚未就绪")
        return self.engine

    def close(self) -> None:
        with self.lock:
            try:
                if self.engine:
                    if self.playing:
                        try:
                            self.pause()
                        except Exception:
                            if self.runtime:
                                self.runtime._ready_for_snapshot = False
                    # A Quick Boot now keeps the game process. Release this
                    # controller's handles before saving its paused warm state.
                    for handle in tuple(self._snapshot_handles):
                        try:
                            self.snapshot_release(handle)
                        except Exception:
                            if self.runtime:
                                self.runtime._ready_for_snapshot = False
                    self.engine.close()
            finally:
                self._snapshot_handles.clear()
                self.engine = None
                self.playing = False
                if self.runtime:
                    self.runtime.close()
