"""Run the pinned Clashback renderer and recorder controls in the Linux guest."""

import ctypes
from concurrent.futures import Future
import json
import os
from pathlib import Path
import runpy
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace


PREFIX = "CR-RECORDER "
session = None
started = Event()


def pointer(address):
    return ctypes.c_uint64.from_address(address).value


def integer(address):
    return ctypes.c_int32.from_address(address).value


class Recorder:
    def __init__(self, context):
        from env import CrxEnv
        from observe import Reader

        self.g, self.bindings = context["g"], context["B"]
        self.manager = context["manager"]
        self.live = context["live"]
        self.on_update_thread = context["on_update_thread"]
        self.prepared_state = 0
        self.initialization_error = None
        self.env = CrxEnv.__new__(CrxEnv)
        self.env.g, self.env.B = self.g, self.bindings
        self.env.reader = Reader(self.g)
        self.env.inject_log, self.env.ability_log, self.env.scheduled = [], [], []
        self.env.stalled = False
        self.live.speed = 1e-6
        self.stream = None
        self.snapshots = {}
        self.snapshot_id = 0

        # The offline fixture contains the replay and its commands. The
        # stock initialization also checks for a separate online battle log.
        site = self.g.addr(0xDEDD0F)
        if self.bindings.build != "160635001" or ctypes.string_at(site, 2) != b"\x75\x16":
            raise RuntimeError("Clashback render initialization does not match the pinned build")
        ctypes.memmove(site, b"\xeb\x16", 2)
        context["every_update"].append(self.prepare_battlefield)

    def prepare_battlefield(self):
        from gate3 import engine_string, ensure_battlefield

        if integer(self.manager + self.bindings.field("manager.state_type")) != 4:
            return
        state = pointer(self.manager + self.bindings.field("manager.state"))
        if not state or state == self.prepared_state or self.initialization_error:
            return
        battle = pointer(state + self.bindings.field("state.battle"))
        replay = pointer(self.manager + self.bindings.field("manager.replay"))
        if not battle or not replay:
            return
        try:
            key = engine_string(self.g, "battle")
            try:
                data = self.g.call(self.bindings.fn("json_get_object"), replay, ctypes.addressof(key), ctypes.c_bool(True))
            finally:
                self.g.call(self.bindings.fn("string_destroy"), ctypes.addressof(key))
            if not data:
                raise RuntimeError("Clashback replay has no battle configuration")
            ensure_battlefield(self.g, battle, data)
            self.prepared_state = state
        except Exception as error:
            self.initialization_error = error
            raise

    def synchronize(self, function):
        result = Future()

        def invoke():
            try:
                result.set_result(function())
            except Exception as error:
                result.set_exception(error)

        self.on_update_thread(invoke)
        return result.result(timeout=25)

    def active_battle(self):
        if self.initialization_error:
            raise self.initialization_error
        if integer(self.manager + self.bindings.field("manager.state_type")) != 4:
            return False
        state = pointer(self.manager + self.bindings.field("manager.state"))
        if state != self.prepared_state or not state:
            return False
        battle = pointer(state + self.bindings.field("state.battle"))
        if not battle:
            return False
        reader = self.env.reader
        logic = reader.logic(battle)
        if not logic or not all(reader.player(logic, side) and
                                pointer(logic + self.bindings.field("logic.deck") + side * 8)
                                for side in (0, 1)):
            return False
        self.env.state, self.env.battle = state, battle
        return True

    def observe(self):
        if not self.active_battle():
            return None
        if self.live.paused and self.live.seek_target is None:
            self.freeze()
        result = self.env.observe()
        result["frame"] = self.live.frames
        runtime = self.env.reader.players_lean(self.env.battle)
        reader = self.env.reader
        logic = reader.logic(self.env.battle)
        for player in result["players"]:
            owner = player["owner"]
            player["runtime"] = runtime[owner]
            abilities = runtime[owner]["abilityRuntime"]
            for ability in abilities:
                native_player = reader.player(logic, owner)
                controller = pointer(native_player + reader.f["player.ability_controllers"] +
                                     (ability["controllerSlot"] - 1) * 8)
                action = pointer(controller + reader.f["controller.action_data"])
                ability["actionDataGlobalId"] = reader.card_id(action) if action else 0
                ability["available"] = (ability["actionDataGlobalId"] > 0 and ability["buttonState"] in (2, 4)
                                        and ability["remainingChargesRaw"] != 0)
            for card in player["hand"]:
                selection = self.env._selection(owner, card["deckSlot"])
                if selection:
                    card["parameter"] = selection[1]
        return result

    def opening(self):
        presentation_started = None

        def ready():
            nonlocal presentation_started
            if not self.active_battle() or integer(self.env.state + 0x30) != 9 or self.live.tick < 0:
                return None
            now = time.monotonic()
            if presentation_started is None:
                presentation_started = now
            # Keep the battle clock nearly stopped while the opening camera and
            # banners finish on the presentation clock.
            if now - presentation_started < 4.5:
                return None
            self.freeze()
            self.live.speed = 1
            return self.observe()

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = self.synchronize(ready)
            if state:
                return state
            time.sleep(0.05)
        raise TimeoutError("Clashback did not initialize the native battle")

    def freeze(self):
        self.live.paused = True
        # The update hook has already latched dt before running this callback,
        # and reloads this slot when the callback returns. Cancel that update
        # too; clearing only scale/extra permits a tick after the receipt.
        ctypes.c_float.from_address(self.live.dt_slot).value = 0
        ctypes.c_float.from_address(self.live.dt_slot + 16).value = 0
        ctypes.c_float.from_address(self.live.dt_slot + 20).value = 0

    def snapshot(self):
        from neo_replay.native_snapshots import NativeSnapshotStream

        if not self.active_battle() or not self.live.paused or len(self.snapshots) >= 34:
            raise RuntimeError("Clashback snapshot is unavailable")
        self.freeze()
        if not self.stream:
            self.stream = NativeSnapshotStream(self.g, self.bindings)
        self.snapshot_id += 1
        tick = self.env.tick()
        self.snapshots[self.snapshot_id] = (tick, self.stream.save(self.env.battle))
        return {"ok": True, "handle": self.snapshot_id, "tick": tick}

    def restore(self, handle):
        from live import ACC_FIELD
        from neo_replay.native_presentation import SnapshotPresentation

        if not self.active_battle() or not self.live.paused or handle not in self.snapshots:
            raise RuntimeError("Clashback snapshot does not belong to the current battle")
        self.freeze()
        tick, payload = self.snapshots[handle]
        self.env.scheduled.clear()
        self.stream.load(self.env.battle, payload, pointer(self.env.state + 0x80))
        if self.env.tick() != tick:
            raise RuntimeError("Clashback snapshot restored the wrong tick")
        ctypes.c_float.from_address(self.env.state + ACC_FIELD).value = 0
        presentation = SnapshotPresentation(self.g, self.bindings)
        if self.g.call(self.bindings.fn("replay_spectator_view")):
            presentation.restored()
        return {"ok": True, "state": self.observe()}

    def step(self, count, actions):
        def begin():
            if not self.active_battle() or not self.live.paused:
                raise RuntimeError("Pause Clashback before advancing ticks")
            before = self.observe()
            queued = []
            if not before["ended"]:
                scheduled, self.env.scheduled = self.env.scheduled, []
                for tick, owner, _, entity, _ in scheduled:
                    if tick != before["tick"] + 1 or not self.env._queue_ability(owner, entity, tick):
                        raise RuntimeError("Clashback rejected the queued ability")
                for owner, slot, x, y in actions:
                    card = next((c for c in before["players"][owner]["hand"] if c["handIndex"] == slot), None)
                    accepted = bool(card and self.env._queue_card(owner, slot, x, y, before["tick"]))
                    queued.append((owner, card, accepted))
                self.live.start_seek(before["tick"] + count)
            return before, queued

        before, queued = self.synchronize(begin)
        target = before["tick"] + count
        deadline = time.monotonic() + 30
        while self.live.seek_target is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        after = self.synchronize(self.observe)
        if after["tick"] != target:
            raise RuntimeError(f"Clashback advanced to {after['tick']} instead of {target}")
        executed = [accepted and all(c["deckSlot"] != card["deckSlot"]
                                     for c in after["players"][owner]["hand"])
                    for owner, card, accepted in queued]
        return {"state": after, "executed": executed}

    def control(self, command):
        match command["op"]:
            case "ready":
                return self.opening()
            case "observe":
                return self.synchronize(self.observe)
            case "step":
                return self.step(command["ticks"], command["actions"])
            case "ability":
                action = SimpleNamespace(owner=command["owner"], secondary_index=command["entity"])
                return self.synchronize(lambda: self.env.queue_ability_action_at(action))
            case "pause":
                def pause():
                    self.freeze()
                    return self.observe()
                return self.synchronize(pause)
            case "resume":
                self.synchronize(lambda: setattr(self.live, "paused", False))
                return {}
            case "speed":
                self.synchronize(lambda: setattr(self.live, "speed", command["speed"]))
                return {}
            case "snapshot":
                return self.synchronize(self.snapshot)
            case "restore":
                return self.synchronize(lambda: self.restore(command["handle"]))
            case "release":
                self.synchronize(lambda: self.snapshots.pop(command["handle"], None))
                return {"ok": True}
            case _:
                raise ValueError("Unknown Clashback worker operation")


def setup(context):
    global session
    session = Recorder(context)
    started.set()


def serve():
    for line in sys.stdin:
        request_id = None
        try:
            command = json.loads(line)
            request_id = command.pop("id")
            if command["op"] == "close":
                os._exit(0)
            if not started.wait(timeout=45):
                raise TimeoutError("Clashback render hooks did not initialize")
            # The renderer imports the hook by its module name.
            result = sys.modules["clashback_worker"].session.control(command)
            reply = {"id": request_id, "result": result}
        except Exception as error:
            reply = {"id": request_id, "error": str(error)}
        print(PREFIX + json.dumps(reply, separators=(",", ":")), flush=True)
    os._exit(0)


def main():
    root = Path(__file__).resolve().parent
    game = root / "packed-game"
    output = Path(sys.argv[1])
    replay = output / "match.json"
    sys.path.insert(0, str(root / "vendor/crx"))
    sys.modules["clashback_worker"] = sys.modules[__name__]
    from updater.rendercheck import render_options

    os.environ.update(render_options(replay, output / "live", 360, 640, 9600))
    os.environ.update(CRX_RENDER_HOOK="clashback_worker", CRX_ASSETS=str(game / "assets"),
                      LIBGL_ALWAYS_SOFTWARE="1", NEO_VIEW_OWNER="1")
    import loader

    # Android and glibc lay out addrinfo differently. This offline worker
    # follows Clashback's macOS host and resolves no network addresses.
    loader.stub("getaddrinfo", -2)
    loader.stub("freeaddrinfo")
    Thread(target=serve, daemon=True).start()
    sys.argv = ["native_worker.py", "render", str(game / "engine"), str(output / "work"),
                str(game / "assets"), str(output), str(24 * 60 * 60)]
    runpy.run_path(str(root / "neo_replay/native_worker.py"), run_name="__main__")


if __name__ == "__main__":
    main()
