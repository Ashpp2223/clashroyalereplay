from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from bridge.controller import BattleController


@dataclass
class Card:
    slot: int = 0
    card: int = 26000000
    cost: int = 30000


class FakeEngine:
    def __init__(self):
        self.state = None
        self.reject = False
        self.snapshots = {}
        self.next_sequence = 0
        self.next_handle = 1
        self.reset_count = 0
        self.pending_actions = []

    def reset(self, seed=1):
        self.reset_count += 1
        self.snapshots.clear()
        self.state = self._state(90, ())
        return self.state

    def _state(self, tick, plays):
        player = lambda owner: SimpleNamespace(owner=owner, elixir=50000, hand=(Card(),), cycle=())
        return SimpleNamespace(tick=tick, finalized=False, result=None, crowns=(0, 0),
                               players=(player(0), player(1)), entities=(), plays=plays)

    def step(self, actions=(), ticks=1):
        before = self.state.tick
        accepted = bool(actions) and not self.reject
        plays = tuple(SimpleNamespace(sequence=self.next_sequence + index, tick=before + 1, owner=action.owner,
                                      card=26000000, cost=30000)
                      for index, action in enumerate(actions)) if accepted else ()
        self.next_sequence += len(plays)
        self.state = self._state(before + ticks, plays)
        return SimpleNamespace(state=self.state, advanced=ticks,
                               executed=tuple(accepted for _ in actions))

    def pause(self):
        return self.state

    def resume(self):
        self.state = None
        return {"ok": True}

    def set_speed(self, multiplier):
        return {"ok": True}

    def request(self, command):
        if command == "replay-schedule-clear":
            self.pending_actions.clear()
            return {"ok": True}
        if command == "snapshot-create":
            handle = self.next_handle
            self.next_handle += 1
            self.snapshots[handle] = self.state
            return {"ok": True, "handle": handle, "tick": self.state.tick}
        if command.startswith("restore "):
            self.state = self.snapshots[int(command.split()[1])]
            return {"ok": True}
        return {"ok": True}

    def _capture(self):
        return self.state

    def close(self):
        pass


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.controller = BattleController(engine_factory=lambda: self.engine)
        self.controller.engine = self.engine
        self.controller.reset(41)

    def test_only_confirmed_card_enters_action_receipt(self):
        self.engine.reject = True
        rejected = self.controller.play_card(1, 0, 3500, 13000)
        self.assertFalse(rejected["accepted"])
        self.assertNotIn("action", rejected)
        self.assertEqual(rejected["observation"]["tick"], 91)
        self.engine.reject = False
        accepted = self.controller.play_card(0, 0, 4500, 14000)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["action"]["tick"], 92)
        self.assertEqual(accepted["action"]["nativeCardId"], 26000000)

    def test_replay_rejects_card_mismatch_and_checks_digest(self):
        action = {"tick": 91, "sequence": 0, "owner": 1, "kind": "play-card",
                  "nativeCardId": 26000000, "slot": 0, "nativeX": 3500, "nativeY": 13000}
        result = self.controller.replay(41, [action], 95)
        self.assertEqual(result["tick"], 95)
        self.assertEqual(self.controller.replay(41, [action], 95, result["digest"])["digest"],
                         result["digest"])
        with self.assertRaisesRegex(RuntimeError, "手牌分歧"):
            self.controller.replay(41, [{**action, "nativeCardId": 1}], 95)

    def test_snapshot_is_session_only(self):
        snapshot = self.controller.snapshot_create()
        self.controller.step(4)
        restored = self.controller.snapshot_restore(snapshot["handle"])
        self.assertEqual(restored["tick"], 90)

    def test_restore_discards_future_actions_from_the_previous_branch(self):
        snapshot = self.controller.snapshot_create()
        self.engine.pending_actions.append({"tick": 110, "owner": 1})
        self.controller.snapshot_restore(snapshot["handle"])
        self.assertEqual(self.engine.pending_actions, [])

    def test_runtime_cleanup_runs_even_if_engine_socket_close_fails(self):
        closed = []
        self.controller.runtime = SimpleNamespace(close=lambda: closed.append(True))
        def broken_close():
            raise ConnectionError("socket already lost")
        self.engine.close = broken_close
        with self.assertRaises(ConnectionError):
            self.controller.close()
        self.assertEqual(closed, [True])
        self.assertIsNone(self.controller.engine)

    def test_replay_continues_from_snapshot_without_reset(self):
        base = self.controller.step(5)
        snapshot = self.controller.snapshot_create()
        resets = self.engine.reset_count
        action = {"tick": 97, "sequence": 42, "owner": 1, "kind": "play-card",
                  "nativeCardId": 26000000, "slot": 0, "nativeX": 3500, "nativeY": 13000}
        result = self.controller.replay(41, [action], 100, snapshot_handle=snapshot["handle"],
                                        base_tick=95, base_digest=base["digest"])
        self.assertEqual(result["tick"], 100)
        self.assertEqual(self.engine.reset_count, resets)
        with self.assertRaisesRegex(RuntimeError, "快照基点状态不一致"):
            self.controller.replay(41, [action], 100, snapshot_handle=snapshot["handle"],
                                   base_tick=95, base_digest="wrong")

    def test_replay_does_not_confuse_event_cursor_with_saved_action_order(self):
        self.engine.next_sequence = 17
        action = {"tick": 91, "sequence": 99, "owner": 1, "kind": "play-card",
                  "nativeCardId": 26000000, "slot": 0, "nativeX": 3500, "nativeY": 13000}
        result = self.controller.replay(41, [action], 91)
        self.assertEqual(result["tick"], 91)
        self.assertEqual(self.engine.state.plays[0].sequence, 17)


class NativeCaptureContractTest(unittest.TestCase):
    def setUp(self):
        from bridge.engine_adapter import new_engine
        new_engine()  # Load the actual vendored decoder without connecting.
        from native_engine.protocol import State
        self.decode = State.decode

    def test_old_probe_capture_is_rejected_with_a_version_error(self):
        with self.assertRaisesRegex(RuntimeError, "cr-minimal.v1"):
            self.decode({"ok": True, "schema": "cr-minimal.v1"})

    def test_native_error_is_preserved(self):
        with self.assertRaisesRegex(RuntimeError, "manager-or-generation"):
            self.decode({"ok": False, "error": "manager-or-generation"})

    def test_current_tower_record_decodes(self):
        state = self.decode({"ok": True, "schema": "cr-minimal.v3", "generation": 1, "tick": 90,
                             "finalized": False, "result": None, "crowns": [0, 0], "players": [],
                             "entities": [[5000000, 0, -1, 9000, 3000, 2400, 2400, None, 0, "building", 0]],
                             "plays": [], "next": 1})
        self.assertEqual(state.entities[0].entity_type, "building")
        self.assertEqual(state.entities[0].hp, 2400)


if __name__ == "__main__":
    unittest.main()
