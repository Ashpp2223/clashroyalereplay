"""Compare rendered 1x/4x replay and probe scheduling on isolated emulator-5560."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter


DECK = [26000000, 26000001, 26000010, 26000030, 26000031, 28000008, 28000011, 28000017]
CONFIG = {"deck0": DECK, "deck1": DECK, "forms0": [0] * 8, "forms1": [2, 1, 0, 0, 0, 0, 0, 0], "level": 16}
# A deterministic Knight Hero activation; every card, form, entity and ability
# is still checked by BattleController against the real native state.
PREFIX = [
    {"tick": tick, "sequence": index + 1, "owner": 1, "kind": "play-card", "nativeCardId": card,
     "slot": slot, "nativeX": 4500, "nativeY": 26000, "nativeForm": form}
    for index, (tick, card, slot, form) in enumerate([
        (111, 26000030, 1, 0), (132, 26000031, 2, 0), (153, 26000010, 3, 0),
        (174, 28000017, 0, 0), (195, 26000000, 0, 2)])
] + [{"tick": 231, "sequence": 6, "owner": 1, "kind": "activate-ability",
      "nativeEntityId": 5000011, "nativeAbilityId": 1171272209}]


class ReplayProfile:
    def __init__(self, engine):
        self.engine = engine
        self.originals = {}
        self.timings = defaultdict(float)
        self.counts = defaultdict(int)
        self.states = {}
        self.plays = {}
        self.resetting = False
        self.last_progress = 0

    def __enter__(self):
        from bridge.controller import observation
        self.started = perf_counter()
        for name in ("request", "reset", "_capture"):
            self.originals[name] = getattr(self.engine, name)

        def request(command):
            kind = "rebuild" if self.resetting else command.split()[0]
            started = perf_counter()
            try:
                result = self.originals["request"](command)
                tick = result.get("tick", 0)
                if kind == 'advance-native' and tick >= self.last_progress + 1000:
                    print(f"replay advance: tick={tick}", flush=True)
                    self.last_progress = tick
                return result
            finally:
                self.timings[kind] += (perf_counter() - started) * 1000
                self.counts[kind] += 1

        def reset(*args, **kwargs):
            started = perf_counter()
            self.resetting = True
            try:
                return self.originals["reset"](*args, **kwargs)
            finally:
                self.timings["resetTotal"] += (perf_counter() - started) * 1000
                self.resetting = False

        def capture(*args, **kwargs):
            state = self.originals["_capture"](*args, **kwargs)
            self.states[str(state.tick)] = observation(state)["digest"]
            for play in state.plays:
                self.plays[play.sequence] = [play.tick, play.owner, play.card, play.form]
            return state

        self.engine.request = request
        self.engine.reset = reset
        self.engine._capture = capture
        return self

    def __exit__(self, *_):
        self.elapsed = (perf_counter() - self.started) * 1000
        for name, original in self.originals.items():
            setattr(self.engine, name, original)

    def result(self):
        return {"totalMs": round(self.elapsed, 2), "rebuildMs": round(self.timings["resetTotal"], 2),
                "requestMs": {key: round(value, 2) for key, value in self.timings.items() if key != "resetTotal"},
                "requestCounts": dict(self.counts)}


def record(controller, path):
    from bridge.controller import observation
    actions = [dict(action) for action in PREFIX]
    with ReplayProfile(controller.engine) as profile:
        state = controller.replay(41, actions, 241, **CONFIG)
        prefix_state = state
        controller.speed(4)
        # Make the route long without relying on a particular winner or a
        # tower's survival under a fixture-specific attack strategy.
        state = controller.step(2200 - state["tick"])
        index = 0
        while not state["finalized"] and state["tick"] < 15000:
            transition = controller.engine.step(ticks=100)
            state = observation(transition.state)
            if state["finalized"]:
                break
            for owner in (0, 1):
                player = state["players"][owner]
                affordable = [card for card in player["hand"] if card["cost"] * 10000 <= player["elixir"]]
                if not affordable:
                    continue
                card = min(affordable, key=lambda card: (0 if index % 5 == 0 and card["card"] >= 28000000 else 1, card["cost"], card["slot"]))
                y = (29000 if owner == 0 else 3000) if card["card"] >= 28000000 else (14000 if owner == 0 else 18000)
                receipt = controller.play_card(owner, card["slot"], 4500, y)
                assert receipt["accepted"], receipt
                actions.append(receipt["action"])
                state = receipt["observation"]
                if state['finalized']:
                    break
            index += 1
            if index % 10 == 0:
                print(f"record: tick={state['tick']}, actions={len(actions)}", flush=True)
        assert state["finalized"] and state["tick"] >= 2200, state
        assert any(action["kind"] == "activate-ability" for action in actions)
    controller.speed(1)
    ticks = {str(action["tick"]) for action in actions} | {str(state["tick"])}
    fixture = {"schema": "cr-long-replay-fixture.v1", "identity": controller.status()["identity"],
               "config": CONFIG, "actions": actions, "final": state, "prefix": prefix_state,
               "digests": {tick: profile.states[tick] for tick in ticks}, "recording": profile.result()}
    path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"recorded: tick={state['tick']}, actions={len(actions)}, digest={state['digest']}", flush=True)
    return fixture


def replay(controller, fixture, speed):
    controller.replay_speed = speed
    controller.speed(0.5)
    with ReplayProfile(controller.engine) as profile:
        state = controller.replay(41, fixture["actions"], fixture["final"]["tick"], fixture["final"]["digest"], **CONFIG)
    # A terminal digest alone could hide intermediate differences. Check every
    # card/ability boundary, including the Hero's cooldown/charge consumption.
    for tick, digest in fixture["digests"].items():
        assert profile.states.get(tick) == digest, f"{speed}x state divergence at tick {tick}"
    assert state["finalized"] and state["crowns"] == fixture["final"]["crowns"]
    assert state["result"] == fixture["final"]["result"]
    status = controller.engine.request("status")
    assert status["paused"] and status["speed"] == 0.5, status
    result = {**profile.result(), "speed": speed, "verifiedDigests": len(fixture["digests"]),
              "checkedDigests": {tick: profile.states[tick] for tick in fixture['digests']},
              "tick": state["tick"], "digest": state["digest"], "finalized": state["finalized"],
              "crowns": state["crowns"], "result": state["result"]}
    print(json.dumps({"phase": f"replay-{speed}x", **result}), flush=True)
    return result


def scheduled_experiment(controller, fixture):
    # Experimental only: the existing queue selects cards by ID and does not
    # verify the archive's expected slot, or bind abilities by saved entity ID.
    # Keep the ability prefix on the production per-action validation path.
    from bridge.controller import observation
    controller.replay_speed = 4
    controller.speed(1)
    remaining = [action for action in fixture["actions"] if action["tick"] > 241]
    assert all(action["kind"] == "play-card" for action in remaining)
    expected_events = [[action["tick"], action["owner"], action["nativeCardId"], action["nativeForm"]] for action in remaining]
    with ReplayProfile(controller.engine) as profile:
        controller.replay(41, PREFIX, 241, fixture["prefix"]["digest"], **CONFIG)
        profile.plays.clear()
        controller.speed(4)
        receipts = []
        assert controller.engine.request("replay-schedule-clear")["ok"]
        for action in remaining:
            command = f"replay-schedule-card {action['owner']} {action['nativeCardId']} {action['nativeX']} {action['nativeY']} {action['tick']}"
            ack = controller.engine.request(command)
            assert ack.get("ok"), ack
            receipts.append((action, ack["sequence"]))
        checkpoints = sorted({action["tick"] for action in remaining[::10]} | {fixture["final"]["tick"]})
        for target in checkpoints:
            state = observation(controller.engine.step(ticks=target - controller.engine.state.tick).state)
            assert state["tick"] == target and state["digest"] == fixture["digests"][str(target)], f"scheduled divergence at {target}"
        for action, sequence in receipts:
            receipt = controller.engine.request(f"replay-schedule-status {sequence}")
            assert receipt.get("state") == "succeeded" and receipt.get("error") == "none", receipt
            assert receipt["executeTick"] == action["tick"] and receipt["owner"] == action["owner"]
            assert receipt["cardId"] == action["nativeCardId"]
            assert receipt["resolvedCardParameter"] & 0x0f == action["nativeForm"]
        assert sorted(profile.plays.values()) == sorted(expected_events)
        assert controller.engine.request("replay-schedule-clear")["ok"]
        controller.speed(1)
    return {**profile.result(), "fromTick": 90, "scheduledFromTick": 241, "verifiedEvents": len(expected_events), "verifiedDigests": len(checkpoints),
            "digest": state["digest"], "productionEnabled": False,
            "limitation": "Existing queue does not validate saved hand slots or bind saved ability entity IDs"}


def branches(controller, fixture):
    controller.replay_speed = 4
    controller.speed(0.25)
    invalid_ability = {**PREFIX[-1], 'nativeEntityId': PREFIX[-1]['nativeEntityId'] + 1}
    try:
        controller.replay(41, [*PREFIX[:-1], invalid_ability], 241, **CONFIG)
    except RuntimeError as error:
        assert '重放技能不可用' in str(error), error
    else:
        raise AssertionError('Replay accepted a different ability entity')
    assert controller.engine.request('status')['speed'] == 0.25
    # The ability prefix spends nearly all elixir. Allow regeneration before
    # creating both alternative card branches from an identical checkpoint.
    base = controller.replay(41, PREFIX, 441, **CONFIG)
    snapshot = controller.snapshot_create()
    controller.speed(4)
    states, actions, after_cards = [], [], []
    for x in (4500, 13500):
        restored = controller.snapshot_restore(snapshot["handle"])
        player = restored["players"][1]
        card = min((card for card in player["hand"] if card["cost"] * 10000 <= player["elixir"]), key=lambda card: card["cost"])
        receipt = controller.play_card(1, card["slot"], x, 18000)
        assert receipt["accepted"]
        actions.append(receipt["action"])
        after_cards.append(receipt['observation'])
        states.append(controller.step(80))
    assert states[0]["digest"] != states[1]["digest"]
    controller.speed(0.25)
    for action, expected in zip(actions, states):
        state = controller.replay(41, [action], expected["tick"], expected["digest"], snapshot["handle"], base["tick"], base["digest"], **CONFIG)
        assert state["digest"] == expected["digest"]
        assert controller.engine.request("status")["speed"] == 0.25
    wrong_slot = {**actions[0], 'slot': (actions[0]['slot'] + 1) % 4}
    try:
        controller.replay(41, [wrong_slot], states[0]['tick'], snapshot_handle=snapshot['handle'],
                          base_tick=base['tick'], base_digest=base['digest'], **CONFIG)
    except RuntimeError as error:
        assert '重放手牌分歧' in str(error), error
    else:
        raise AssertionError('Replay accepted a different hand slot')
    # The generic probe queue still executes that card by ID, demonstrating
    # why a correct terminal result alone cannot replace archive validation.
    action = actions[0]
    queued = controller.engine.request(f"replay-schedule-card 1 {action['nativeCardId']} {action['nativeX']} {action['nativeY']} {action['tick']}")
    assert queued['ok']
    queued_state = controller.step(1, expected_tick=base['tick'])
    assert queued_state['digest'] == after_cards[0]['digest']
    with ReplayProfile(controller.engine) as profile:
        restored = controller.replay(41, [], base["tick"], base["digest"], snapshot["handle"], base["tick"], base["digest"], **CONFIG)
    assert restored["digest"] == base["digest"]
    assert not profile.counts["speed"] and not profile.counts["advance-native"]
    controller.snapshot_release(snapshot["handle"])
    return {"baseTick": base["tick"], "branchDigests": [state["digest"] for state in states],
            "exactSnapshotMs": round(profile.elapsed, 2), "exactSnapshotAddsSpeedOrAdvanceCalls": False,
            'invalidAbilityEntityRejected': True, 'invalidHandSlotRejected': True, 'probeQueueOmitsSavedSlotGuard': True}


def warmup(controller):
    samples = []
    reference = None
    # A freshly started probe has no rendered match yet; speed commands require
    # that first scene to exist. Keep this setup reset outside the timed trials.
    controller.replay_speed = 1
    controller.reset(41, **CONFIG)
    controller.speed(0.5)
    for speed in (1, 4, 1, 4):
        controller.replay_speed = speed
        with ReplayProfile(controller.engine) as profile:
            root = controller.reset(41, **CONFIG)
        if reference is None:
            reference = root['digest']
        assert root['tick'] == 90 and root['digest'] == reference
        native = controller.engine.request('status')
        assert native['paused'] and native['speed'] == 0.5
        samples.append({'speed': speed, **profile.result()})
    return {'rootDigest': reference, 'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(".local-runtime/long-replay/report.json"))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--baseline-report", type=Path, help="Reuse a verified 1x baseline for this exact fixture")
    args = parser.parse_args()
    config = json.loads((args.runtime / "runtime.json").read_text(encoding="utf-8-sig"))
    if config.get("serial") != "emulator-5560":
        raise ValueError("Long replay checks require the dedicated emulator-5560")
    os.environ["CR_RUNTIME_HOME"] = str(args.runtime.resolve())
    os.environ["CR_ANDROID_SERIAL"] = "emulator-5560"
    os.environ["CR_ENGINE_PORT"] = "26790"
    from bridge.controller import BattleController
    from bridge.runtime import AndroidRuntime
    args.report.parent.mkdir(parents=True, exist_ok=True)
    fixture_path = args.fixture or args.report.with_name("route.json")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    controller = BattleController(AndroidRuntime())
    report = {"status": "running", "mode": "native-rendered", "checks": []}
    root = Path(__file__).resolve().parents[1]
    report['sourceSha256'] = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in (Path(__file__).resolve(), root / 'bridge/controller.py', root / 'vendor/clash-royale-battle-engine/engine.py')}

    def save():
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        started = perf_counter()
        controller.start()
        report["startupMs"] = round((perf_counter() - started) * 1000, 2)
        report["identity"] = controller.status()["identity"]
        report['phase'] = 'recording'
        save()
        fixture = json.loads(fixture_path.read_text(encoding="utf-8")) if fixture_path.exists() else record(controller, fixture_path)
        assert fixture["identity"] == report["identity"] and fixture["config"] == CONFIG
        report["fixture"] = {"path": str(fixture_path), "sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                             "actions": len(fixture["actions"]), "tick": fixture["final"]["tick"]}
        report['phase'] = 'warmup-comparison'
        save()
        report['warmup'] = warmup(controller)
        report['checks'].append('1x-and-4x-warmup-root-digest-and-playback-clock')
        report['phase'] = 'baseline-1x'
        save()
        if args.baseline_report:
            reference = json.loads(args.baseline_report.read_text(encoding='utf-8'))
            assert reference['identity'] == report['identity']
            assert reference['fixture']['sha256'] == report['fixture']['sha256']
            assert reference['baseline']['checkedDigests'] == fixture['digests']
            assert reference['baseline']['finalized'] and reference['baseline']['digest'] == fixture['final']['digest']
            report['baseline'] = reference['baseline']
            report['baselineReference'] = {'path': str(args.baseline_report), 'sha256': hashlib.sha256(args.baseline_report.read_bytes()).hexdigest()}
        else:
            report["baseline"] = replay(controller, fixture, 1)
        report["checks"].append("1x-every-card-ability-and-terminal-digest")
        save()
        report['phase'] = 'accelerated-4x'
        save()
        report["accelerated"] = replay(controller, fixture, 4)
        report["checks"].append("4x-every-card-ability-and-terminal-digest")
        save()
        report['phase'] = 'scheduled-experiment'
        save()
        report["scheduledExperiment"] = scheduled_experiment(controller, fixture)
        report["checks"].append("probe-scheduled-card-events-forms-checkpoints-and-terminal")
        save()
        report['phase'] = 'cross-branch'
        save()
        report["branches"] = branches(controller, fixture)
        report["checks"].extend(["cross-branch-snapshot-suffix-digests", "chosen-playback-speed-restored", "exact-cached-node-path-unchanged"])
        report['checks'].extend(['invalid-ability-entity-rejected', 'invalid-hand-slot-rejected', 'generic-probe-queue-slot-guard-limitation-demonstrated'])
        image, _ = controller.runtime.frame(fresh=True)
        args.report.with_suffix(".png").write_bytes(image)
        report.update(status='passed', phase='complete')
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        try:
            controller.close()
        finally:
            save()
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
