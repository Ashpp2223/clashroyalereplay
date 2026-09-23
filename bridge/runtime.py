"""Discover and attest the one supported Android native battle runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from .video import NativeFrameStream


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "clash-royale-battle-engine"
MANIFEST = json.loads((VENDOR / "vendor" / "firstlight" / "supported_engine.json").read_text())
UPSTREAM_COMMIT = "f92ccbecd6a1c9ad1fa3abaca54a51110607b6df"
PROBE_SHA256 = "04fa085250598e20096dde4eb4ce4eb7cebe2b8408925c0c05e691e50047c8fe"
AVD_NAME = "CR_Null_ARM64_API30"
DEVICE_PORT = 26789


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def android_sdk() -> Path:
    for candidate in (os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"),
                      str(Path.home() / "AppData" / "Local" / "Android" / "Sdk")):
        if candidate and (Path(candidate) / "platform-tools" / "adb.exe").exists():
            return Path(candidate)
    raise RuntimeError("未找到 Android SDK；请安装 Android Emulator 和 platform-tools")


class AndroidRuntime:
    def __init__(self, avd_name: str = AVD_NAME):
        self.avd_name = avd_name
        self.sdk = android_sdk()
        self.adb = self.sdk / "platform-tools" / "adb.exe"
        self.emulator = self.sdk / "emulator" / "emulator.exe"
        self.serial: str | None = None
        self.process: subprocess.Popen | None = None
        self.stage = "未启动"
        self.error: str | None = None
        self.apk_sha256 = self._prepared_apk_hash()
        self.video: NativeFrameStream | None = None

    @staticmethod
    def _prepared_apk_hash() -> str:
        prepared = ROOT / ".local-runtime" / "prepared.json"
        if prepared.exists():
            data = json.loads(prepared.read_text(encoding="utf-8-sig"))
            path = Path(data.get("path", ""))
            expected = data.get("apkSha256", "").lower()
            if path.is_file() and len(expected) == 64 and sha256(path) == expected:
                return expected
        return MANIFEST["apk_sha256"]

    def command(self, *args: str, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess:
        if not self.serial:
            raise RuntimeError("未选定 Android 模拟器")
        result = subprocess.run([str(self.adb), "-s", self.serial, *map(str, args)],
                                capture_output=True, text=True, timeout=timeout, check=False)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ADB 命令失败")
        return result

    def start(self) -> dict:
        self.stage = "检查环境"
        local_probe = VENDOR / "prebuilt" / "libcrprobe.so"
        if sha256(local_probe) != PROBE_SHA256:
            raise RuntimeError("本地探针哈希不符")

        self.serial = self._find_arm64_device()
        if not self.serial:
            if not self.emulator.exists():
                raise RuntimeError("Android Emulator 不存在")
            avds = subprocess.run([str(self.emulator), "-list-avds"], capture_output=True,
                                  text=True, timeout=20, check=True).stdout.splitlines()
            if self.avd_name not in avds:
                raise RuntimeError(f"找不到 ARM64 AVD：{self.avd_name}")
            if platform.machine().lower() in ("amd64", "x86_64"):
                raise RuntimeError("官方 Android Emulator 在 x86_64 Windows 上不能运行 ARM64 AVD；请连接可运行 ARM64 应用的 Android 设备或兼容模拟器")
            self.stage = "启动模拟器"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen([str(self.emulator), "-avd", self.avd_name,
                                             "-no-window", "-no-snapshot-load"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=flags)
            self.serial = self._wait_for_avd(120)
        self.stage = "等待 Android 启动"
        self._wait_for_boot(120)
        self.stage = "校验运行时"
        self.command("root", timeout=30)
        self.command("wait-for-device", timeout=30)
        self._check_installed_runtime()
        self.stage = "启动原生探针"
        env = {**os.environ, "PATH": str(self.sdk / "platform-tools") + os.pathsep + os.environ.get("PATH", "")}
        subprocess.run([sys.executable, str(VENDOR / "bootstrap_emulator.py"), "--serial", self.serial,
                        "--timeout", "90"], env=env, capture_output=True, text=True,
                       timeout=120, check=True)
        self.stage = "读取能力声明"
        attest = self._attest()
        self.video = NativeFrameStream(str(self.adb), self.serial)
        self.video.start()
        self.stage = "就绪"
        self.error = None
        return attest

    def _find_arm64_device(self) -> str | None:
        devices = subprocess.run([str(self.adb), "devices"], capture_output=True,
                                 text=True, timeout=15, check=True).stdout
        preferred = os.environ.get("CR_ANDROID_SERIAL")
        for line in devices.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device" or (preferred and parts[0] != preferred):
                continue
            result = subprocess.run([str(self.adb), "-s", parts[0], "shell", "getprop", "ro.product.cpu.abi"],
                                    capture_output=True, text=True, timeout=8, check=False)
            if result.stdout.strip() == "arm64-v8a":
                return parts[0]
        return None

    def _wait_for_avd(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            serial = self._find_arm64_device()
            if serial:
                return serial
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"模拟器退出，代码 {self.process.returncode}")
            time.sleep(1)
        raise TimeoutError("模拟器启动超时")

    def _wait_for_boot(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.command("shell", "getprop", "sys.boot_completed", check=False, timeout=10)
            if result.stdout.strip() == "1":
                return
            time.sleep(1)
        raise TimeoutError("Android 系统启动超时")

    def _check_installed_runtime(self) -> None:
        abi = self.command("shell", "getprop", "ro.product.cpu.abi").stdout.strip()
        if abi != MANIFEST["abi"]:
            raise RuntimeError(f"ABI 不匹配：{abi}")
        package = MANIFEST["package"]
        path_result = self.command("shell", "pm", "path", package, check=False)
        paths = [line.removeprefix("package:") for line in path_result.stdout.splitlines()]
        base_apk = next((path for path in paths if path.endswith("/base.apk")), None)
        if not base_apk:
            self._install_local_prepared()
            path_result = self.command("shell", "pm", "path", package)
            paths = [line.removeprefix("package:") for line in path_result.stdout.splitlines()]
            base_apk = next((path for path in paths if path.endswith("/base.apk")), None)
            if not base_apk:
                raise RuntimeError("准备好的游戏 APK 安装后仍不可用")
        directory = base_apk.rsplit("/", 1)[0]
        checks = {
            "APK": (base_apk, self.apk_sha256),
            "libg": (directory + "/lib/arm64/libg.so", MANIFEST["libg_sha256"]),
            "探针": (directory + "/lib/arm64/libcrprobe.so", PROBE_SHA256),
        }
        for label, (path, expected) in checks.items():
            result = self.command("shell", "sha256sum", path, check=False)
            actual = result.stdout.split()[0].lower() if result.stdout.split() else ""
            if actual != expected:
                raise RuntimeError(f"{label} 哈希不符或文件缺失：{actual or '未找到'}")

    def _install_local_prepared(self) -> None:
        if not self.serial or not (self.serial.startswith("emulator-") or self.serial.startswith("127.0.0.1:")):
            raise RuntimeError("连接的是物理设备；请手动安装经过探针准备的游戏 APK")
        prepared = ROOT / ".local-runtime" / "prepared.json"
        if not prepared.exists():
            raise RuntimeError("未安装经过探针准备的游戏 APK；请先运行准备脚本")
        data = json.loads(prepared.read_text(encoding="utf-8-sig"))
        path = Path(data.get("path", ""))
        if not path.is_file() or sha256(path) != self.apk_sha256:
            raise RuntimeError("本地准备的 APK 缺失或哈希不符")
        self.stage = "安装原生战斗运行时"
        self.command("install", str(path), timeout=180)

    def _attest(self) -> dict:
        from .engine_adapter import new_engine
        engine = new_engine()
        try:
            data = engine.request("attest")
        finally:
            engine.close()
        claim = data.get("attestation", {})
        expected = {
            "package_name": MANIFEST["package"],
            "abi": MANIFEST["abi"],
            "probe_sha256": PROBE_SHA256,
            "libg_sha256": MANIFEST["libg_sha256"],
            "content_fingerprint_sha256": MANIFEST["runtime_fingerprint_sha256"],
            "content_version": MANIFEST["runtime_content_version"],
        }
        for field, value in expected.items():
            if claim.get(field) != value:
                raise RuntimeError(f"运行时证明不符：{field}")
        required = ("native_render", "snapshot_restore", "structured_observation", "two_sided_actions")
        if not claim.get("production_ready") or any(not claim.get("capabilities", {}).get(key) for key in required):
            raise RuntimeError("原生引擎能力未全部就绪")
        return claim

    def frame_png(self) -> bytes:
        if not self.serial:
            raise RuntimeError("模拟器未启动")
        result = subprocess.run([str(self.adb), "-s", self.serial, "exec-out", "screencap", "-p"],
                                capture_output=True, timeout=10, check=True)
        if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("模拟器未返回有效画面")
        return result.stdout

    def frame(self) -> tuple[bytes, str]:
        streamed = self.video.frame() if self.video else None
        if streamed:
            return streamed, "image/jpeg"
        return self.frame_png(), "image/png"

    def close(self) -> None:
        if self.video:
            self.video.close()
            self.video = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process = None
