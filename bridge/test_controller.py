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

    def reset(self, seed=1):
        self.state = self._state(90, ())
        return self.state

    def _state(self, tick, plays):
        player = lambda owner: SimpleNamespace(owner=owner, elixir=50000, hand=(Card(),), cycle=())
        return SimpleNamespace(tick=tick, finalized=False, result=None, crowns=(0, 0),
                               players=(player(0), player(1)), entities=(), plays=plays)

    def step(self, actions=(), ticks=1):
        before = self.state.tick
        accepted = bool(actions) and not self.reject
        plays = tuple(SimpleNamespace(sequence=index, tick=before + 1, owner=action.owner,
                                      card=26000000, cost=30000)
                      for index, action in enumerate(actions)) if accepted else ()
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
        if command == "snapshot-create":
            handle = len(self.snapshots) + 1
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


if __name__ == "__main__":
    unittest.main()
