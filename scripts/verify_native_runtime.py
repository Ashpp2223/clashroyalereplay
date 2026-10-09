"""Exercise real battle, HTTP commands, live snapshots and cold replay on Android."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
import threading
from time import perf_counter, sleep
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from bridge.controller import BattleController
from bridge.runtime import AndroidRuntime, ROOT
from bridge.server import BridgeServer


def verify_spell_battle() -> dict:
    # AndroidRuntime has already loaded the pinned native_engine package.
    from native_engine.engine import Engine, Play
    engine = Engine()
    executions, spells, refused = [0, 0], [0, 0], []
    try:
        state = engine.reset(2)
        if state.tick != 90:
            raise AssertionError("法术对局未到达首次可操作边界")
        peak_entities = len(state.entities)
        for index in range(160):
            if state.finalized:
                break
            plays, cards = [], []
            for player in state.players:
                affordable = [card for card in player.hand if card.cost * 10000 <= player.elixir]
                if affordable:
                    card = min(affordable, key=lambda card: (0 if index % 5 == 0 and card.card >= 28000000 else 1,
                                                            card.cost))
                    plays.append(Play(player.owner, card.slot, 4500, 14000 if player.owner == 0 else 18000))
                    cards.append(card.card)
            tick = state.tick
            transition = engine.step(plays, 50)
            state = transition.state
            for action, card, executed in zip(plays, cards, transition.executed):
                if executed:
                    executions[action.owner] += 1
                    if card >= 28000000:
                        spells[action.owner] += 1
                else:
                    refused.append({"tick": tick + 1, "owner": action.owner, "card": card})
            peak_entities = max(peak_entities, len(state.entities))
        if not state.finalized or not all(spells) or refused:
            raise AssertionError(f"法术终局检查失败：tick={state.tick}, spells={spells}, refused={refused}")
        return {"seed": 2, "profile": "compact-v3-headless", "finalTick": state.tick,
                "finalized": state.finalized, "result": state.result, "crowns": list(state.crowns),
                "executions": executions, "spellExecutions": spells, "peakEntities": peak_entities}
    finally:
        engine.close()


def verify(report_path: Path) -> dict:
    runtime = AndroidRuntime()
    controller = BattleController(runtime)
    server = None
    thread = None
    report = {"status": "running", "phase": "native-startup", "checks": [], "timingsMs": {}}
    started = perf_counter()
    try:
        controller.start()
        report["serial"] = runtime.serial
        report["androidVersion"] = runtime.command("shell", "getprop", "ro.build.version.release").stdout.strip()
        report["identity"] = controller.status()["identity"]
        report["checks"].append("attested-native-runtime")
        report["timingsMs"]["startup"] = round((perf_counter() - started) * 1000)
        token = secrets.token_hex(32)
        server = BridgeServer(0, token, controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def request(path: str, body: dict | None = None):
            req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=120) as response:
                    return json.load(response)
            except HTTPError as error:
                detail = json.load(error).get("error", str(error))
                raise RuntimeError(f"{path}: {detail}") from error

        def play(state, owner: int, x: int = 4500):
            player = state["players"][owner]
            hand = [card for card in player["hand"] if card["card"] < 28000000 and
                    card["cost"] * 10000 <= player["elixir"]]
            if not hand:
                raise AssertionError("标准对局没有可负担的单位牌")
            card = min(hand, key=lambda candidate: (candidate["cost"], candidate["slot"]))
            result = request("/v1/play-card", {"owner": owner, "slot": card["slot"], "x": x,
                                               "y": 14000 if owner == 0 else 18000})
            if not result["accepted"]:
                raise AssertionError(f"玩家 {owner} 的出牌被原版引擎拒绝")
            return result

        report["phase"] = "native-battle-reset"
        state = request("/v1/reset", {"seed": 1})
        if state["tick"] != 90:
            raise AssertionError("原版战斗未到达首次可操作边界")
        root = request("/v1/step", {"ticks": 10})
        report["phase"] = "native-snapshots-and-actions"
        root_snapshot = request("/v1/snapshot-create", {})
        first = play(root, 0)
        second = play(first["observation"], 1)
        actions = [first["action"], second["action"]]
        branch_a = request("/v1/step", {"ticks": 60})
        branch_snapshot = request("/v1/snapshot-create", {})
        report["checks"].extend(["first-playable-tick-90", "two-sided-card-execution"])
        report["phase"] = "native-snapshot-replay"
        before = perf_counter()
        restored = request("/v1/snapshot-restore", {"handle": root_snapshot["handle"]})
        report["timingsMs"]["snapshotRestore"] = round((perf_counter() - before) * 1000)
        if restored["digest"] != root["digest"]:
            raise AssertionError("恢复根节点后状态不一致")
        alternative = play(restored, 0, 13500)
        branch_b = request("/v1/step", {"ticks": branch_a["tick"] - alternative["observation"]["tick"]})
        if branch_b["digest"] == branch_a["digest"]:
            raise AssertionError("不同分支的场上状态未产生变化")
        restored_a = request("/v1/snapshot-restore", {"handle": branch_snapshot["handle"]})
        if restored_a["digest"] != branch_a["digest"]:
            raise AssertionError("跨分支恢复后状态不一致")
        hot = request("/v1/replay", {"seed": 1, "actions": actions, "targetTick": branch_a["tick"],
                                    "expectedDigest": branch_a["digest"], "snapshotHandle": root_snapshot["handle"],
                                    "baseTick": root["tick"], "baseDigest": root["digest"]})
        if hot["digest"] != branch_a["digest"]:
            raise AssertionError("快照后缀重放不一致")
        report["timingsMs"]["branchChecks"] = round((perf_counter() - before) * 1000)
        report["checks"].extend(["branch-state-divergence", "cross-branch-snapshot-restore", "snapshot-suffix-replay"])
        for snapshot in (root_snapshot, branch_snapshot):
            request("/v1/snapshot-release", {"handle": snapshot["handle"]})
        before = perf_counter()
        report["phase"] = "seed-and-action-log-replay"
        cold = request("/v1/replay", {"seed": 1, "actions": actions, "targetTick": branch_a["tick"],
                                     "expectedDigest": branch_a["digest"]})
        if cold["digest"] != branch_a["digest"]:
            raise AssertionError("从种子和动作日志恢复不一致")
        report["timingsMs"]["coldReplay"] = round((perf_counter() - before) * 1000)
        report["checks"].append("seed-and-action-log-replay")
        report["phase"] = "native-playback"
        request("/v1/speed", {"multiplier": 2})
        request("/v1/resume", {})
        sleep(0.4)
        paused = request("/v1/pause", {})
        if paused["tick"] <= cold["tick"]:
            raise AssertionError("原版播放未推进战斗")
        report["checks"].append("native-play-pause-speed")
        report["phase"] = "process-restart-action-log-replay"
        before = perf_counter()
        prior_pid = runtime.command("shell", "pidof", "nullsroyale.rel.free").stdout.strip()
        controller.close()
        controller.start()
        current_pid = runtime.command("shell", "pidof", "nullsroyale.rel.free").stdout.strip()
        reopened = request("/v1/replay", {"seed": 1, "actions": actions, "targetTick": branch_a["tick"],
                                         "expectedDigest": branch_a["digest"]})
        if reopened["digest"] != branch_a["digest"]:
            raise AssertionError("重新启动游戏进程后，动作日志恢复不一致")
        report["gameProcessIds"] = [prior_pid, current_pid]
        report["timingsMs"]["processRestartReplay"] = round((perf_counter() - before) * 1000)
        report["checks"].append("game-process-restart-action-log-replay")
        report["phase"] = "native-frame"
        req = Request(base + "/v1/frame", headers={"Authorization": "Bearer " + token})
        with urlopen(req, timeout=20) as response:
            frame = response.read()
            kind = response.headers.get_content_type()
        if not frame.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8")):
            raise AssertionError("桥接没有返回原版游戏画面")
        image_path = report_path.with_suffix(".png" if kind == "image/png" else ".jpg")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(frame)
        report["framePath"] = str(image_path)
        report["checks"].append("native-frame-over-http")
        report["phase"] = "headless-native-spell-terminal-battle"
        before = perf_counter()
        # A lane owns one mode. Retire the rendered scene before starting the
        # independent headless fixture; an in-process mode switch can stall.
        controller.close()
        controller.start()
        report["spellBattle"] = verify_spell_battle()
        report["timingsMs"]["spellBattle"] = round((perf_counter() - before) * 1000)
        report["checks"].extend(["two-sided-native-spells", "headless-native-terminal-result"])
        report.update(status="passed", phase="complete", finalTick=reopened["tick"], replayDigest=reopened["digest"])
    except Exception as error:
        report.update(status="failed", stage=runtime.stage, error=str(error))
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        controller.close()
        report["timingsMs"]["total"] = round((perf_counter() - started) * 1000)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / ".local-runtime/native-runtime-check.json")
    args = parser.parse_args()
    report = verify(args.report)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
