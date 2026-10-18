"""Opt-in CN 38.4.5 runtime from the verified adjacent research workspace."""
from __future__ import annotations

import hashlib
import contextlib
import io
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .paths import ROOT


def research_root() -> Path:
    return Path(os.environ.get("CR_CAMP_RESEARCH_ROOT") or ROOT.parent / "clashroyale1016").resolve()


def load_profile() -> dict:
    root = research_root()
    profile = json.loads((root / "analysis/recorder-profile.json").read_text(encoding="utf-8"))
    if profile.get("schema") != "cr-camp-recorder-profile.v1" or profile.get("id") != "cn-38.4.5":
        raise RuntimeError("国服引擎配置身份无效")
    adapter = root / "analysis/recorder_adapter.py"
    if hashlib.sha256(adapter.read_bytes()).hexdigest() != profile["adapterSha256"]:
        raise RuntimeError("国服适配器已变化，请重新验证并生成接入配置")
    bundled = json.loads((ROOT/"src/data/cnEngineProfile.json").read_text(encoding="utf-8"))
    if any(profile[key] != bundled[key] for key in ("id","identity","catalog","cropProfile")):
        raise RuntimeError("国服前端配置已过期，请重新导出已验证配置")
    return profile


def research_modules():
    analysis = research_root() / "analysis"
    if str(analysis) not in sys.path:
        sys.path.insert(0, str(analysis))
    result = []
    for name in ("research_runtime", "native_control"):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve().parent != analysis:
            raise RuntimeError("加载了其他目录的国服研究接口")
        result.append(module)
    return result


def new_engine(port):
    load_profile()
    research_modules()
    module = importlib.import_module("recorder_adapter")
    if Path(module.__file__).resolve().parent != research_root() / "analysis":
        raise RuntimeError("国服适配器目录不匹配")
    return module.RecorderEngine(port=port)


class CampRuntime:
    """Own the recorder connection and video, leaving research AVD data intact."""
    def __init__(self):
        self.profile = load_profile()
        self.stage = "未启动"
        self.stage_code = "idle"
        self.apk_sha256 = self.profile["identity"]["apkSha256"]
        self.runtime_fingerprint_sha256 = self.profile["identity"]["runtimeFingerprintSha256"]
        self.serial = "emulator-5572"
        self.video = None
        self.process = None
        self.owns_session = False
        self._ready_for_snapshot = False
        self.environment_configured = (research_root()/".runtime/runtime.json").is_file()
        self.resources_configured = (research_root()/".runtime/content/verified.json").is_file()

    def start(self):
        from .engine_adapter import engine_port
        from .video import NativeFrameStream
        research, control = research_modules()
        self.stage = "连接国服研究环境"; self.stage_code = "emulator"
        # Tauri consumes one handshake line then closes the stdout reader.
        # Research CLI progress must never write into that retired pipe.
        with contextlib.redirect_stdout(io.StringIO()):
            research.start()
            research.ready()
        self.adb = research.ADB
        self.stage = "校验国服引擎"; self.stage_code = "probe"
        path = research.adb("shell", "pm", "path", "cn.crhistory.app").stdout.strip()
        if not path.startswith("package:/data/app/") or "\n" in path:
            raise RuntimeError("专用设备未安装已验证的国服研究 APK")
        installed = research.adb("shell", "sha256sum", path[8:]).stdout.split()[0]
        if installed != self.apk_sha256:
            raise RuntimeError("国服 APK 摘要与接入配置不符，请重新构建并验证")
        active = json.loads(research.adb("shell", "cat", "/data/user/0/cn.crhistory.app/files/replay/active-run.json").stdout)
        if active.get("creator") != "offline-research":
            raise RuntimeError("设备当前运行目录不属于离线研究环境")
        run_id = active.get("id", "")
        import re
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run_id):
            raise RuntimeError("国服研究运行目录无效")
        fingerprint = f"/data/user/0/cn.crhistory.app/files/replay/runs/{run_id}/update/fingerprint.json"
        if research.adb("shell", "sha256sum", fingerprint).stdout.split()[0] != self.runtime_fingerprint_sha256:
            raise RuntimeError("国服素材指纹与接入配置不符")
        research.adb("forward", f"tcp:{engine_port()}", "tcp:26791")
        if not research.adb("shell", "pidof", "cn.crhistory.app:replay", check=False).stdout.strip():
            research.adb("shell", "am", "start", "-W", "-n",
                         "cn.crhistory.app/com.supercell.clashroyale.GameApp", timeout=90)
        deadline = time.monotonic() + 45
        while True:
            probe = control.NativeControl(port=engine_port())
            try:
                if probe.status().get("ready"):
                    state = probe.observe()
                    if state["mode"] == "headless":
                        fixture = json.loads((research.ROOT/"fixtures/terminal-empty.json").read_text())
                        probe.configure(fixture)
                    info = probe.render_info()
                    if info["flipped"] or info["physicalSize"] != [1080,1920]:
                        raise RuntimeError("国服画面方向或尺寸与接入配置不符")
                    points = {tuple(p["world"]):p["pixel"] for p in info["points"]}
                    x,y = points[(0,0)]
                    world = [x,y,points[(18000,0)][0]-x,points[(0,32000)][1]-y]
                    if world[2]<=0 or world[3]<=0 or not (0<=x<1080 and 0<=y<1920 and x+world[2]<=1080 and y+world[3]<=1920):
                        raise RuntimeError("国服原生投影超出有效画面")
                    break
            except (OSError, ConnectionError):
                if time.monotonic() >= deadline: raise
            finally:
                probe.close()
            if time.monotonic() >= deadline: raise TimeoutError("国服引擎未完成初始化")
            time.sleep(.2)
        if self.video: self.video.close()
        self.video = NativeFrameStream(str(self.adb), self.serial)
        self.video.start()
        self.stage = "国服引擎就绪"; self.stage_code = "ready"
        return {"capabilities":{"exactStep":True,"snapshots":True,"abilities":True,"nativeRenderer":True},
                "identity":self.profile["identity"]}

    def frame_png(self):
        from .runtime import rgba_png
        if self.stage_code != "ready": raise RuntimeError("国服引擎尚未就绪")
        raw = subprocess.run([str(self.adb),"-s",self.serial,"exec-out","screencap"],
            capture_output=True,check=True,timeout=15,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)).stdout
        return rgba_png(raw,compression=0)

    def frame(self, fresh=False):
        streamed = self.video.frame() if self.video and not fresh else None
        return (streamed,"image/jpeg") if streamed else (self.frame_png(),"image/png")

    def close(self):
        if self.video: self.video.close(); self.video = None
        self._ready_for_snapshot = False
        self.stage = "未启动"; self.stage_code = "idle"
