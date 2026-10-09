"""Serial, confirmed battle operations and deterministic route replay."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from .engine_adapter import new_engine, new_play
from .runtime import AndroidRuntime, MANIFEST, PROBE_SHA256, UPSTREAM_COMMIT


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
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**facts, "digest": digest}


class BattleController:
    def __init__(self, runtime: AndroidRuntime | None = None, engine_factory=new_engine):
        self.runtime = runtime
        self.engine_factory = engine_factory
        self.engine = None
        self.lock = threading.RLock()
        self.playing = False
        self.seed: int | None = None
        self.attestation: dict | None = None
        self.last_error: str | None = None

    def status(self) -> dict:
        # Metadata reads must remain available during a long serialized replay.
        return {
            "stage": self.runtime.stage if self.runtime else "测试环境",
            "ready": self.engine is not None,
            "playing": self.playing,
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

    def start(self) -> dict:
        with self.lock:
            if self.engine is not None:
                self.last_error = None
                return self.status()
        if self.runtime is None:
            raise RuntimeError("无 Android 运行时")
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

    def reset(self, seed: int) -> dict:
        with self.lock:
            if not isinstance(seed, int) or seed < 0 or seed > 2**31 - 1:
                raise ValueError("随机种子无效")
            engine = self._engine()
            state = engine.reset(seed=seed)
            self.seed = seed
            self.playing = False
            return observation(state)

    def observe(self) -> dict:
        with self.lock:
            engine = self._engine()
            state = engine._capture() if self.playing else engine.state
            if state is None:
                raise RuntimeError("尚未开始战斗")
            return observation(state)

    def pause(self) -> dict:
        with self.lock:
            state = self._engine().pause()
            self.playing = False
            return observation(state)

    def resume(self) -> dict:
        with self.lock:
            self._engine().resume()
            self.playing = True
            self.last_error = None
            return {"playing": True}

    def speed(self, multiplier: float) -> dict:
        with self.lock:
            if multiplier not in (0.25, 0.5, 1, 2, 4):
                raise ValueError("不支持的播放速度")
            result = self._engine().set_speed(multiplier)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "变速失败"))
            return {"speed": multiplier}

    def step(self, ticks: int = 1) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            if not isinstance(ticks, int) or not 1 <= ticks <= 1000000:
                raise ValueError("步进 tick 无效")
            transition = self._engine().step(ticks=ticks)
            if transition.advanced != ticks:
                raise RuntimeError("实际推进 tick 与请求不符")
            return observation(transition.state)

    def play_card(self, owner: int, slot: int, x: int, y: int) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            if owner not in (0, 1) or not isinstance(slot, int) or slot not in range(4):
                raise ValueError("玩家或手牌槽位无效")
            if not all(isinstance(value, int) for value in (x, y)) or not 0 <= x <= 18000 or not 0 <= y <= 32000:
                raise ValueError("落点坐标无效")
            engine = self._engine()
            before = engine.state
            if before is None:
                raise RuntimeError("战斗尚未暂停")
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
                           "slot": slot, "nativeX": x, "nativeY": y},
            }

    def snapshot_create(self) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
            result = self._engine().request("snapshot-create")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "创建快照失败"))
            return result

    def snapshot_restore(self, handle: int) -> dict:
        with self.lock:
            if self.playing:
                self.pause()
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
            return result

    def replay(self, seed: int, actions: list[dict], target_tick: int, expected_digest: str | None = None,
               snapshot_handle: int | None = None, base_tick: int | None = None,
               base_digest: str | None = None) -> dict:
        """Replay an event suffix from a live snapshot, or the full path from the seed."""
        with self.lock:
            if snapshot_handle is None:
                state = self.reset(seed)
            else:
                if self.seed != seed or not isinstance(base_tick, int) or not base_digest:
                    raise ValueError("快照基点无效")
                state = self.snapshot_restore(snapshot_handle)
                if state["tick"] != base_tick or state["digest"] != base_digest:
                    raise RuntimeError("快照基点状态不一致")
            if not isinstance(target_tick, int) or target_tick < state["tick"]:
                raise ValueError("目标 tick 早于重放基点")
            ordered = sorted(actions, key=lambda action: (action["tick"], action["sequence"]))
            if ordered != actions:
                raise ValueError("动作日志顺序无效")
            offset = 0
            while offset < len(ordered):
                tick = ordered[offset]["tick"]
                group = []
                while offset < len(ordered) and ordered[offset]["tick"] == tick:
                    group.append(ordered[offset])
                    offset += 1
                if any(action.get("kind") != "play-card" for action in group) or tick > target_tick:
                    raise ValueError("动作不受当前引擎支持或晚于目标 tick")
                current = self._engine().state.tick
                if tick <= current:
                    raise ValueError("动作 tick 不可重放")
                gap = tick - current - 1
                if gap:
                    self.step(gap)
                before = self._engine().state
                for action in group:
                    card = next((card for card in before.players[action["owner"]].hand
                                 if card.slot == action["slot"]), None)
                    if card is None or card.card != action["nativeCardId"]:
                        raise RuntimeError(f"重放手牌分歧：tick {tick}")
                transition = self._engine().step(
                    [new_play(a["owner"], a["slot"], a["nativeX"], a["nativeY"]) for a in group], ticks=1)
                if transition.advanced != 1 or not all(transition.executed):
                    raise RuntimeError(f"重放动作被拒绝：tick {tick}")
                actual = [(event.owner, event.card) for event in transition.state.plays
                          if event.tick == tick]
                expected = [(a["owner"], a["nativeCardId"]) for a in group]
                if actual != expected:
                    raise RuntimeError(f"重放事件分歧：tick {tick}")
            remaining = target_tick - self._engine().state.tick
            if remaining:
                self.step(remaining)
            result = self.observe()
            if expected_digest and result["digest"] != expected_digest:
                raise RuntimeError(f"重放状态摘要不一致：tick {target_tick}")
            return result

    def _engine(self):
        if self.engine is None:
            raise RuntimeError("原生引擎尚未就绪")
        return self.engine

    def close(self) -> None:
        with self.lock:
            try:
                if self.engine:
                    self.engine.close()
            finally:
                self.engine = None
                self.playing = False
                if self.runtime:
                    self.runtime.close()
