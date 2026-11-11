"""Windows adapter for the pinned Clashback engine in its dedicated Android guest."""

import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import struct
import subprocess
from tempfile import TemporaryDirectory
from threading import Condition, Thread
import time
from types import SimpleNamespace
from uuid import uuid4

from .paths import ROOT
from .runtime import android_sdk, rgba_png


PROFILE = "clashback-160635001"
AVD = "CR_Clashback_160635001"
SERIAL = "emulator-5576"
GUEST = "/data/local/clashback-linux"
APP = "/opt/clashback"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PREFIX = "CR-RECORDER "
FRAME_SIZE = 64 + 360 * 640 * 4
# One read-only ADB pipe per battle. The renderer atomically replaces this file;
# sending only changed frames avoids repeatedly transferring paused pixels.
FRAME_READER = """
import pathlib, sys, time
path = pathlib.Path(sys.argv[1])
previous = None
changed_at = time.monotonic()
while True:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if previous is not None:
            break
        data = b''
    if len(data) == 921664 and data[:4] == b'CRXF' and data[4:8] != previous:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        previous = data[4:8]
        changed_at = time.monotonic()
    if time.monotonic() - changed_at > 5:
        break
    time.sleep(1 / 30)
"""


def load_profile():
    return json.loads((ROOT / "src/data/clashbackEngineProfile.json").read_text(encoding="utf-8"))


class ClashbackRuntime:
    def __init__(self):
        self.home = Path(os.environ.get("CR_CLASHBACK_ROOT") or ROOT / ".local-runtime" / PROFILE)
        self.profile = load_profile()
        self.identity = self.profile["identity"]
        self.apk_sha256 = self.identity["apkSha256"]
        self.runtime_fingerprint_sha256 = self.identity["runtimeFingerprintSha256"]
        self.environment_configured = (self.home / "avd" / f"{AVD}.ini").is_file()
        self.resources_configured = (self.home / "packed-game/manifest.json").is_file()
        self.stage, self.stage_code = "未启动", "idle"
        self.video = None
        self._ready_for_snapshot = False
        self.emulator = None
        self.folder = None
        self.frame_tick = 0
        self.frame_after = -1
        self._frame_condition = Condition()
        self._frame_process = None
        self._frame_thread = None
        self._frame_data = None
        self._frame_received = 0.0
        self._frame_error = None
        self._frame_png = None

    def android(self, *args, timeout=25, check=True):
        return subprocess.run([str(self.adb), "-s", SERIAL, *map(str, args)], capture_output=True,
                              timeout=timeout, check=check, creationflags=CREATE_NO_WINDOW)

    def _wait_for_device(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self.emulator and self.emulator.poll() is not None:
                raise RuntimeError("Clashback 虚拟机启动失败，请检查 recorder-emulator.log")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Clashback 虚拟机未连接")
            try:
                self.android("wait-for-device", timeout=min(2, remaining))
                return
            except subprocess.TimeoutExpired:
                continue
            except subprocess.CalledProcessError:
                # Transport registration/root restart can reset a read-only
                # wait connection. Keep the same VM and bounded deadline.
                time.sleep(min(0.2, remaining))

    def start(self):
        if not self.environment_configured or not self.resources_configured:
            raise RuntimeError("缺少已验证的 Clashback 本机运行环境")
        sdk = android_sdk()
        self.adb = sdk / "platform-tools/adb.exe"
        self.stage, self.stage_code = "启动 Clashback 专用虚拟机", "emulator"
        # ADB can retain a 'host' or 'offline' transport after the emulator has
        # exited. Verify its console instead of treating that entry as a VM.
        console = self.android("emu", "avd", "name", timeout=3, check=False)
        if console.returncode == 0:
            if console.stdout.decode().splitlines()[:1] != [AVD]:
                raise RuntimeError("Clashback 端口被其他虚拟机占用")
        else:
            env = {**os.environ, "ANDROID_AVD_HOME": str(self.home / "avd")}
            with (self.home / "recorder-emulator.log").open("wb") as log:
                self.emulator = subprocess.Popen(
                    [str(sdk / "emulator/emulator.exe"), "-avd", AVD, "-port", "5576", "-no-window",
                     "-no-snapshot", "-no-audio", "-gpu", "swiftshader_indirect", "-memory", "3072"],
                    env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        self._wait_for_device(120)
        if self.android("emu", "avd", "name").stdout.decode().splitlines()[:1] != [AVD]:
            raise RuntimeError("Clashback 端口被其他虚拟机占用")
        deadline = time.monotonic() + 120
        while self.android("shell", "getprop", "sys.boot_completed").stdout.strip() != b"1":
            if time.monotonic() >= deadline:
                raise TimeoutError("Clashback 虚拟机未完成启动")
            time.sleep(0.5)
        if self.android("shell", "id", "-u").stdout.strip() != b"0":
            self.android("root")
            self._wait_for_device(30)
        if self.android("shell", "id", "-u").stdout.strip() != b"0":
            raise RuntimeError("Clashback 专用虚拟机未启用本机离线运行环境")
        for source, target in (("proc", "proc"), ("/dev", "dev"), ("/sys", "sys")):
            mount = "-t proc" if target == "proc" else "-o bind"
            self.android("shell", f"grep -q ' {GUEST}/{target} ' /proc/mounts || "
                         f"mount {mount} {source} {GUEST}/{target}")
        self.stage, self.stage_code = "校验 Clashback 引擎和资源", "probe"
        for path, digest in (("engine/libg.so", self.identity["libgSha256"]),
                             ("assets/fingerprint.json", self.runtime_fingerprint_sha256)):
            remote = f"{GUEST}{APP}/packed-game/{path}"
            if self.android("shell", "sha256sum", remote).stdout.decode().split()[0] != digest:
                raise RuntimeError(f"Clashback 运行资源校验失败：{path}")
        self.worker = ROOT / "bridge/clashback_worker.py"
        if hashlib.sha256(self.worker.read_text(encoding="utf-8").encode()).hexdigest() != self.identity["probeSha256"]:
            raise RuntimeError("Clashback 控制代码与接入配置不符")
        self.android("push", self.worker, f"{GUEST}{APP}/clashback_worker.py")
        self.stage, self.stage_code = "Clashback 引擎就绪", "ready"
        return {"capabilities": {"exactStep": True, "snapshots": True, "abilities": True, "nativeRenderer": True},
                "identity": self.identity}

    def create_engine(self):
        return ClashbackEngine(self)

    def _read_frames(self, process):
        try:
            while True:
                # Buffered pipe.read fills the requested size, including when
                # ADB delivers a frame in many separate packets.
                data = process.stdout.read(FRAME_SIZE)
                if len(data) != FRAME_SIZE:
                    raise RuntimeError("Clashback 原生画面流已中断")
                magic, _, width, height, _ = struct.unpack_from("<4sIIIi", data)
                if magic != b"CRXF" or (width, height) != (360, 640):
                    raise RuntimeError("Clashback 原生画面格式无效")
                with self._frame_condition:
                    if self._frame_process is not process:
                        return
                    self._frame_data = data
                    self._frame_received = time.monotonic()
                    self._frame_condition.notify_all()
        except Exception as error:
            with self._frame_condition:
                if self._frame_process is process:
                    self._frame_error = str(error)
                    self._frame_condition.notify_all()

    def frame(self, fresh=False):
        with self._frame_condition:
            if not self.folder:
                raise RuntimeError("Clashback 尚未创建对局")
            if self._frame_process is None:
                self._frame_process = subprocess.Popen(
                    [str(self.adb), "-s", SERIAL, "exec-out", "chroot", GUEST,
                     "/usr/bin/python3", "-u", "-c", FRAME_READER,
                     f"{self.folder}/live/frame.bin"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
                self._frame_thread = Thread(target=self._read_frames, args=(self._frame_process,), daemon=True)
                self._frame_thread.start()
            process = self._frame_process
            expected_tick, after = self.frame_tick, self.frame_after
            deadline = time.monotonic() + 5
            while True:
                if self._frame_process is not process:
                    raise RuntimeError("Clashback 对局已切换")
                if self._frame_error or process.poll() is not None:
                    raise RuntimeError(self._frame_error or "Clashback 原生画面流已退出")
                data = self._frame_data
                if data is not None and time.monotonic() - self._frame_received < 1:
                    _, frame, width, height, tick = struct.unpack_from("<4sIIIi", data)
                    matches = tick == expected_tick and frame > after if fresh else tick >= 0
                    if matches:
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Clashback 原生画面尚未就绪")
                self._frame_condition.wait(min(remaining, 0.1))
            if self._frame_png is None or self._frame_png[0] is not data:
                stride = width * 4
                top_down = b"".join(data[start:start + stride] for start in range(len(data) - stride, 63, -stride))
                self._frame_png = (data, rgba_png(struct.pack("<III", width, height, 1) + top_down))
            return self._frame_png[1], "image/png"

    def stop_frames(self):
        with self._frame_condition:
            process, self._frame_process = self._frame_process, None
            thread, self._frame_thread = self._frame_thread, None
            self._frame_data = self._frame_png = self._frame_error = None
            self.folder = None
            self._frame_condition.notify_all()
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if thread:
                thread.join(timeout=3)
            process.stdout.close()

    def close(self):
        self.stop_frames()
        if self.emulator and self.emulator.poll() is None:
            self.android("emu", "kill", check=False)
            try:
                self.emulator.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.emulator.terminate()
        self.emulator = None
        self.folder = None
        self.stage, self.stage_code = "未启动", "idle"


class ClashbackEngine:
    def __init__(self, runtime):
        self.runtime = runtime
        self.state = None
        self.process = None
        self.sequence = 0
        self.request_id = 0
        self.snapshot_id = 0
        self.snapshots = {}
        self.pending_abilities = 0
        self.costs = {c["id"]: c["cost"] for c in runtime.profile["catalog"]["cards"]}
        self.omitted = {c["id"] for c in runtime.profile["catalog"]["cards"] if c["omitFromStartingHand"]}

    def matches_deal(self, actual, requested):
        # Clashback places starting-hand exclusions in its own draw positions.
        # Match the four visible cards and the order of the ordinary draws.
        return all(
            observed[:4] == target[:4] and sorted(observed[4:]) == sorted(target[4:])
            and tuple(card for card in observed[4:] if card not in self.omitted)
            == tuple(card for card in target[4:] if card not in self.omitted)
            for observed, target in zip(actual, requested, strict=True)
        )

    def _read(self, process, replies):
        try:
            with (self.runtime.home / "recorder-engine.log").open("w", encoding="utf-8") as log:
                for line in process.stdout:
                    if line.startswith(PREFIX):
                        replies.put(json.loads(line[len(PREFIX):]))
                    else:
                        log.write(line)
                        log.flush()
        finally:
            replies.put(None)

    def _call(self, op, **kwargs):
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("Clashback 原生对局进程已退出")
        self.request_id += 1
        request = {"id": self.request_id, "op": op, **kwargs}
        try:
            self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            reply = self.replies.get(timeout=75)
        except (OSError, Empty) as error:
            self.close()
            raise RuntimeError("Clashback 原生对局响应中断") from error
        if reply is None or reply.get("id") != self.request_id:
            self.close()
            raise RuntimeError("Clashback 原生对局进程退出或响应顺序错误")
        if "error" in reply:
            if op in ("step", "restore", "pause"):
                self.close()
            raise RuntimeError(reply["error"])
        return reply["result"]

    def reset(self, seed=1, deck0=None, deck1=None, forms0=None, forms1=None, level=None,
              tower0=None, tower1=None, *, warmup_speed=1):
        self.close()
        self.sequence = 0
        self.pending_abilities = 0
        template = self.runtime.home / "source/neo_replay/standard_match.json"
        match = json.loads(template.read_text(encoding="utf-8"))
        match.update(rndSeed=seed, endTick=9600)
        battle = match["battle"]
        if level is not None:
            battle["lvlcap"] = battle["cardlvlmin"] = level
        for owner, (deck, forms, tower) in enumerate(((deck0, forms0, tower0), (deck1, forms1, tower1))):
            if deck is not None:
                battle[f"deck{owner}"]["sp"] = [{"d": card, "el": form} for card, form in zip(deck, forms or [0] * 8)]
            if tower is not None:
                battle[f"deck{owner}"]["sc"][0]["d"] = tower
            if level is not None:
                battle[f"avatar{owner}"]["expLevel"] = level
                battle["hbd"][owner]["kt"] = level
        self.runtime.folder = f"{APP}/recorder-{uuid4().hex}"
        self.runtime.frame_tick = 0
        self.runtime.frame_after = -1
        folder = self.runtime.folder
        try:
            self.runtime.android("shell", "mkdir", f"{GUEST}{folder}")
            with TemporaryDirectory() as tmp:
                fixture = Path(tmp) / "match.json"
                fixture.write_text(json.dumps(match, separators=(",", ":")), encoding="utf-8")
                self.runtime.android("push", fixture, f"{GUEST}{folder}/match.json")
            self.process = subprocess.Popen(
                [str(self.runtime.adb), "-s", SERIAL, "shell", "-T", "chroot", GUEST, "/usr/bin/env",
                 "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "/usr/bin/python3", "-u", f"{APP}/clashback_worker.py", folder],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
            self.replies = Queue()
            Thread(target=self._read, args=(self.process, self.replies), daemon=True).start()
            return self._state(self._call("ready"))
        except Exception:
            self.close()
            raise

    def _state(self, result, plays=()):
        if result is None:
            raise RuntimeError("Clashback 原生对局未就绪")
        players = []
        for player in result["players"]:
            owner = player["owner"]
            hand = [SimpleNamespace(slot=c["handIndex"], card=c["cardId"], parameter=c.get("parameter", 0),
                                    cost=c["parameter"] >> 28 if "parameter" in c else self.costs.get(c["cardId"], 0))
                    for c in player["hand"]]
            runtime = player["runtime"]
            runtime["abilityRuntime"] = [a for a in runtime["abilityRuntime"] if a["actionDataGlobalId"] > 0]
            for ability in runtime["abilityRuntime"]:
                ability["championEntityKeys"] = [dict(owner=owner, objectIndex=-2, secondaryIndex=id)
                                                 for id in ability.pop("championObjectIds")]
            players.append(SimpleNamespace(owner=owner, elixir=player["elixirRaw"], hand=hand,
                                           cycle=[c["cardId"] for c in player["cycle"]], runtime=runtime))
        entities = [SimpleNamespace(id=e["nativeObjectId"], owner=e["owner"], card=e["cardId"],
                                    x=e["x"], y=e["y"], hp=e["hp"], max_hp=e["maxHp"])
                    for e in result["objects"]]
        self.state = SimpleNamespace(tick=result["tick"], players=players, entities=entities,
                                     finalized=result["ended"], result=result["winner"], crowns=result["crownsRaw"],
                                     plays=plays, next_sequence=self.sequence)
        self.runtime.frame_tick = self.state.tick
        self.runtime.frame_after = result["frame"]
        return self.state

    def _capture(self):
        return self._state(self._call("observe"))

    def step(self, actions=(), ticks=5):
        before = self.state
        result = self._call("step", ticks=ticks, actions=[[a.owner, a.slot, a.x, a.y] for a in actions])
        self.sequence += self.pending_abilities
        self.pending_abilities = 0
        played = []
        for action, accepted in zip(actions, result["executed"], strict=True):
            if accepted:
                card = next(c for c in before.players[action.owner].hand if c.slot == action.slot)
                played.append(SimpleNamespace(owner=action.owner, card=card.card, form=card.parameter & 0xf,
                                              tick=before.tick + 1, sequence=self.sequence))
                self.sequence += 1
        state = self._state(result["state"], played)
        return SimpleNamespace(state=state, advanced=state.tick - before.tick, executed=result["executed"])

    def pause(self):
        paused = self._call("pause")
        # The native callback freezes future dt before the current update has
        # consumed its latched dt. Its immediate receipt can be one tick early.
        # Cross a rendered-frame boundary using read-only observations before
        # exposing the paused tick or asking for its exact frame.
        deadline = time.monotonic() + 5
        while True:
            state = self._call("observe")
            if state and state["frame"] > paused["frame"]:
                return self._state(state)
            if time.monotonic() >= deadline:
                self.close()
                raise RuntimeError("Clashback 暂停未完成，请恢复连接")

    def resume(self):
        self._call("resume")
        return {"ok": True}

    def set_speed(self, multiplier):
        self._call("speed", speed=multiplier)
        return {"ok": True}

    def request(self, command):
        parts = command.split()
        if parts[0] == "activate-ability":
            result = self._call("ability", owner=int(parts[1]), entity=int(parts[3]))
            if result["ok"]:
                self.pending_abilities += 1
            return result
        if parts[0] == "snapshot-create":
            result = self._call("snapshot")
            self.snapshot_id += 1
            self.snapshots[self.snapshot_id] = (result["handle"], self.sequence)
            return {**result, "handle": self.snapshot_id}
        if parts[0] == "restore":
            snapshot = self.snapshots.get(int(parts[1]))
            if not snapshot:
                return {"ok": False, "error": "Clashback 快照不属于当前对局"}
            result = self._call("restore", handle=snapshot[0])
            self.sequence = snapshot[1]
            self._state(result["state"])
            return {"ok": True}
        if parts[0] == "release-snapshot":
            snapshot = self.snapshots.pop(int(parts[1]), None)
            if snapshot:
                self._call("release", handle=snapshot[0])
            return {"ok": True}
        if parts[0] == "replay-schedule-clear":
            self.pending_abilities = 0
            return {"ok": True}
        return {"ok": False, "error": "Clashback 不支持此控制命令"}

    def close(self):
        folder = self.runtime.folder
        self.runtime.stop_frames()
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                try:
                    process.stdin.write('{"id":0,"op":"close"}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
        if folder:
            self.runtime.android("shell", "rm", "-rf", f"{GUEST}{folder}", check=False)
        self.state = None
        self.snapshots.clear()
