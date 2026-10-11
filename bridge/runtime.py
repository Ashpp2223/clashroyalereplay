"""Manage the development Android environment and attest the pinned battle engine."""

from __future__ import annotations

import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import zlib

from .video import NativeFrameStream
from .paths import ROOT, VENDOR, RUNTIME_HOME


MANIFEST = json.loads((VENDOR / "vendor" / "firstlight" / "supported_engine.json").read_text())
CONTENT_LOCK = json.loads((ROOT / "bridge/content_lock.json").read_text(encoding="utf-8"))
UPSTREAM_COMMIT = "f92ccbecd6a1c9ad1fa3abaca54a51110607b6df"
PROBE_SHA256 = "421d6f13dd060c15e94a180f78da9dbcfcd4dde088613f4bca215e021d9bb4e1"
AVD_NAME = "CR_Dev_API31"
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
    raise RuntimeError("未找到 Android platform-tools（adb）")


class AndroidRuntime:
    def __init__(self, avd_name: str = AVD_NAME):
        self.avd_name = avd_name
        # Keep the HTTP bridge available even before Android tools are installed.
        self.sdk = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
                        str(Path.home() / "AppData/Local/Android/Sdk"))
        self.adb = Path(os.environ.get("CR_ADB") or str(self.sdk / "platform-tools/adb.exe"))
        self.emulator = self.sdk / "emulator" / "emulator.exe"
        self.serial: str | None = None
        self.process: subprocess.Popen | None = None
        self.stage = "未启动"
        self.stage_code = "idle"
        self.error: str | None = None
        self.apk_sha256 = self._prepared_apk_hash()
        self.video: NativeFrameStream | None = None
        self.frame_encoder: str | None = None
        self.owns_session = False
        self.runtime_fingerprint_sha256 = MANIFEST["runtime_fingerprint_sha256"]
        config_path = RUNTIME_HOME / "runtime.json"
        self.config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        if self.config.get("sdkRoot") and not os.environ.get("ANDROID_SDK_ROOT") and not os.environ.get("ANDROID_HOME"):
            self.sdk = Path(self.config["sdkRoot"])
            self.adb = Path(os.environ.get("CR_ADB") or str(self.sdk / "platform-tools/adb.exe"))
            self.emulator = self.sdk / "emulator/emulator.exe"
        self.avd_name = self.config.get("avdName", avd_name)
        self.preferred_serial = os.environ.get("CR_ANDROID_SERIAL") or self.config.get("serial") or "emulator-5556"

    @staticmethod
    def _prepared_apk_hash() -> str:
        prepared = RUNTIME_HOME / "prepared.json"
        if prepared.exists():
            data = json.loads(prepared.read_text(encoding="utf-8-sig"))
            path = Path(data.get("path", ""))
            expected = data.get("apkSha256", "").lower()
            if path.is_file() and re.fullmatch(r"[0-9a-f]{64}", expected):
                return expected
        return MANIFEST["apk_sha256"]

    def command(self, *args: str, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess:
        if not self.serial:
            raise RuntimeError("未选定 Android 模拟器")
        result = subprocess.run([str(self.adb), "-s", self.serial, *map(str, args)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ADB 命令失败")
        return result

    def start(self) -> dict:
        self.stage = "检查环境"
        self.stage_code = "checking"
        if not self.adb.is_file():
            raise RuntimeError("未找到 ADB；请安装 Android platform-tools 或设置 CR_ADB")
        # Hash the local APK in the background startup, after the HTTP greeting.
        prepared = RUNTIME_HOME / "prepared.json"
        if prepared.is_file():
            payload = json.loads(prepared.read_text(encoding="utf-8-sig"))
            path = Path(payload.get("path", ""))
            if not path.is_file() or sha256(path) != self.apk_sha256:
                raise RuntimeError("本地准备的 APK 缺失或哈希不符")
        local_probe = VENDOR / "prebuilt" / "libcrprobe.so"
        if sha256(local_probe) != PROBE_SHA256:
            raise RuntimeError("本地探针哈希不符")

        self.serial = self._find_arm64_device()
        if not self.serial:
            self._connect_selected_device()
            self.serial = self._find_arm64_device()
        if not self.serial:
            self._launch_sdk_emulator()
            self.serial = self._wait_for_avd(120)
        self.stage = "等待 Android 启动"
        self.stage_code = "android"
        self._wait_for_boot(120)
        self.stage = "校验运行时"
        self.stage_code = "verifying"
        root = self.command("root", timeout=30, check=False)
        if root.returncode:
            raise RuntimeError("Android 运行环境未开启 ADB root 权限")
        self.command("wait-for-device", timeout=30)
        if self.command("shell", "id", "-u").stdout.strip() != "0":
            raise RuntimeError("Android 运行环境的 ADB 尚无 root 权限")
        if self.serial.startswith("emulator-") and self.config.get("backend") == "android-sdk":
            # This dedicated development device must show the game rather than
            # Android's first-run fullscreen instruction overlay.
            self.command("shell", "settings", "--user", "0", "put", "secure", "immersive_mode_confirmations", "confirmed")
        self._check_installed_runtime()
        self.stage = "启动原生探针"
        self.stage_code = "probe"
        self.owns_session = True
        from .bootstrap_runtime import bootstrap
        from .engine_adapter import engine_port
        bootstrap(str(self.adb), self.serial, port=engine_port(), timeout=90, verified=True)
        self.stage = "读取能力声明"
        self.stage_code = "capabilities"
        attest = self._attest()
        # Resolve the bundled encoder before the first node picture is requested.
        try:
            import imageio_ffmpeg
            self.frame_encoder = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, OSError, RuntimeError):
            self.frame_encoder = None
        self.video = NativeFrameStream(str(self.adb), self.serial)
        self.video.start()
        self.stage = "就绪"
        self.stage_code = "ready"
        self.error = None
        return attest

    def _launch_sdk_emulator(self) -> None:
        if not self.preferred_serial.startswith("emulator-") or not self.emulator.is_file():
            raise RuntimeError("未找到选定的 Android 设备或官方开发模拟器")
        port = int(self.preferred_serial.removeprefix("emulator-"))
        if port < 5554 or port > 5682 or port % 2:
            raise RuntimeError("开发模拟器端口必须为 5554～5682 的偶数")
        avd_home = Path(self.config.get("avdHome", str(RUNTIME_HOME / "avd")))
        if not (avd_home / (self.avd_name + ".ini")).is_file():
            raise RuntimeError("尚未连接战斗运行环境，请点击“设置运行环境”选择已有环境目录")
        self.stage = "启动 Android 开发环境"
        self.stage_code = "emulator"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = RUNTIME_HOME / "dev-emulator.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as output:
            self.process = subprocess.Popen(
                [str(self.emulator), "-avd", self.avd_name, "-port", str(port), "-no-window", "-noaudio",
                 "-no-metrics", "-gpu", self.config.get("gpu", "host"), "-memory", "2048", "-cores", "2"],
                env={**os.environ, "ANDROID_AVD_HOME": str(avd_home)}, stdout=output, stderr=output, creationflags=flags)

    def _connect_selected_device(self) -> None:
        preferred = self.preferred_serial
        endpoints = set()
        if preferred and re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d+", preferred):
            endpoints.add(preferred)
        for endpoint in sorted(endpoints):
            if preferred and endpoint != preferred:
                continue
            host, port_text = endpoint.rsplit(":", 1)
            try:
                with socket.create_connection((host, int(port_text)), timeout=0.3):
                    pass
            except OSError:
                continue
            subprocess.run([str(self.adb), "connect", endpoint], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=10, check=False)

    def _find_arm64_device(self) -> str | None:
        devices = subprocess.run([str(self.adb), "devices"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=15, check=True).stdout
        preferred = self.preferred_serial
        for line in devices.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device" or (preferred and parts[0] != preferred):
                continue
            result = subprocess.run([str(self.adb), "-s", parts[0], "shell", "getprop", "ro.product.cpu.abilist"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False)
            if "arm64-v8a" in result.stdout.strip().split(","):
                return parts[0]
        return None

    def _wait_for_avd(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._connect_selected_device()
            serial = self._find_arm64_device()
            if serial:
                return serial
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"开发模拟器启动失败（{self.process.returncode}）；详情见 .local-runtime/dev-emulator.log")
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
        abi_list = self.command("shell", "getprop", "ro.product.cpu.abilist").stdout.strip().split(",")
        if MANIFEST["abi"] not in abi_list:
            raise RuntimeError(f"运行时不支持 {MANIFEST['abi']}：{abi_list}")
        package = MANIFEST["package"]
        path_result = self.command("shell", "pm", "path", package, check=False)
        paths = [line.removeprefix("package:") for line in path_result.stdout.splitlines()]
        base_apk = next((path for path in paths if path.endswith("/base.apk")), None)
        if not base_apk:
            raise RuntimeError("请先安装未修改的原版游戏，并完成游戏内资源更新")
        directory = base_apk.rsplit("/", 1)[0]
        if self.command("shell", "sha256sum", directory + "/lib/arm64/libg.so").stdout.split()[0] != MANIFEST["libg_sha256"]:
            raise RuntimeError("已安装的 libg.so 与固定引擎版本不符")
        self.stage = "校验游戏资源"
        self.stage_code = "resources"
        self._check_updated_content()
        installed = self.command("shell", "sha256sum", base_apk, timeout=120).stdout.split()[0] == self.apk_sha256
        if not installed:
            self._install_local_prepared()
            base_apk = self.command("shell", "pm", "path", package).stdout.strip().removeprefix("package:")
            directory = base_apk.rsplit("/", 1)[0]
        checks = {
            "APK": (base_apk, self.apk_sha256),
            "libg": (directory + "/lib/arm64/libg.so", MANIFEST["libg_sha256"]),
            "探针": (directory + "/lib/arm64/libcrprobe.so", PROBE_SHA256),
        }
        for label, (path, expected) in checks.items():
            # APK and libg were already checked above. Installing a replacement
            # invalidates those results, so all three are then verified again.
            if installed and label in ("APK", "libg"):
                continue
            result = self.command("shell", "sha256sum", path, timeout=120 if label == "APK" else 30, check=False)
            actual = result.stdout.split()[0].lower() if result.stdout.split() else ""
            if actual != expected:
                raise RuntimeError(f"{label} 哈希不符或文件缺失：{actual or '未找到'}")

    def _install_local_prepared(self) -> None:
        if not self.serial or not (self.serial.startswith("emulator-") or self.serial.startswith("127.0.0.1:")):
            raise RuntimeError("连接的是物理设备；请手动安装经过探针准备的游戏 APK")
        prepared = RUNTIME_HOME / "prepared.json"
        if not prepared.exists():
            raise RuntimeError("未安装经过探针准备的游戏 APK；请先运行准备脚本")
        data = json.loads(prepared.read_text(encoding="utf-8-sig"))
        path = Path(data.get("path", ""))
        if not path.is_file() or sha256(path) != self.apk_sha256:
            raise RuntimeError("本地准备的 APK 缺失或哈希不符")
        self.stage = "安装原生战斗运行时"
        self.stage_code = "installing"
        from .install_runtime import Device, install
        receipt = RUNTIME_HOME / ("installed-" + re.sub(r"[^a-zA-Z0-9-]", "_", self.serial) + ".json")
        install(Device(str(self.adb), self.serial), path, receipt)

    def _check_updated_content(self) -> None:
        from .install_runtime import Device, apk_path, base_asset_hashes, recover_pending_downloads, resource_hashes, validate_resources
        device = Device(str(self.adb), self.serial)
        hashes = resource_hashes(device, required_only=True)
        hashes = recover_pending_downloads(device, hashes)
        try:
            validate_resources(hashes, base_asset_hashes(device, hashes, apk_path(device)))
        except ValueError as error:
            if hashes.get("fingerprint.json") in CONTENT_LOCK["fingerprints"]:
                raise RuntimeError(str(error)) from error
        else:
            self.runtime_fingerprint_sha256 = hashes["fingerprint.json"]
            return
        fingerprint = f"/data/data/{MANIFEST['package']}/update/fingerprint.json"
        result = self.command("shell", "sha256sum", fingerprint, check=False)
        actual = result.stdout.split()[0].lower() if result.stdout.split() else ""
        if actual not in CONTENT_LOCK["fingerprints"]:
            content = self.command("shell", "cat", fingerprint, check=False)
            try:
                version = str(json.loads(content.stdout).get("version", "未知"))
            except (ValueError, TypeError):
                version = "未下载"
            if version == MANIFEST["runtime_content_version"]:
                raise RuntimeError("游戏资源版本号相同，但内容指纹不符；请使用匹配的更新资源")
            raise RuntimeError(
                f"当前游戏资源为 {version}，复盘引擎需要 {MANIFEST['runtime_content_version']}。"
                "请先打开未修改的原版游戏，让它完成资源更新，再启动复盘器"
            )
        raise RuntimeError("游戏资源文件未完整校验")

    def _attest(self) -> dict:
        from .engine_adapter import new_engine
        engine = new_engine()
        try:
            data = engine.request("attest")
        finally:
            engine.close()
        if not data.get("ok"):
            raise RuntimeError("探针未提供运行时证明：" + str(data.get("error", "无有效响应")))
        claim = data.get("attestation", {})
        expected = {
            "package_name": MANIFEST["package"],
            "abi": MANIFEST["abi"],
            "probe_sha256": PROBE_SHA256,
            "libg_sha256": MANIFEST["libg_sha256"],
            "content_fingerprint_sha256": self.runtime_fingerprint_sha256,
            "content_assets_sha256": CONTENT_LOCK["requiredFiles"]["assets.scdb"],
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
        # Android's PNG encoder dominates paused-node display latency. Transfer
        # lightly compressed pixels and encode on the host, keeping a fresh capture.
        try:
            if not self.frame_encoder:
                raise RuntimeError("截图编码器不可用")
            result = subprocess.run(
                [str(self.adb), "-s", self.serial, "exec-out", "sh", "-c", "screencap | gzip -1"],
                capture_output=True, timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw = gzip.decompress(result.stdout)
            width, height, pixel_format = struct.unpack_from("<III", raw)
            header = len(raw) - width * height * 4
            if pixel_format != 1 or not (0 < width <= 8192 and 0 < height <= 8192) or header not in (12, 16):
                raise ValueError("不支持的原生截图格式")
            encoded = subprocess.run(
                [self.frame_encoder, "-hide_banner", "-loglevel", "error",
                 "-threads", "1", "-f", "rawvideo", "-pixel_format", "rgba",
                 "-video_size", f"{width}x{height}", "-i", "pipe:0", "-frames:v", "1",
                 "-threads", "1", "-compression_level", "6", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                input=raw[header:], capture_output=True, timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if not encoded.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("截图编码失败")
            return encoded.stdout
        except (OSError, ValueError, EOFError, struct.error, zlib.error, subprocess.SubprocessError, ImportError, RuntimeError):
            # Older devices and unavailable host decoders retain the PNG path.
            pass
        result = subprocess.run([str(self.adb), "-s", self.serial, "exec-out", "screencap", "-p"],
                                capture_output=True, timeout=10, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("模拟器未返回有效画面")
        return result.stdout

    def frame(self, fresh: bool = False) -> tuple[bytes, str]:
        if fresh:
            return self.frame_png(), "image/png"
        streamed = self.video.frame() if self.video else None
        if streamed:
            return streamed, "image/jpeg"
        return self.frame_png(), "image/png"

    def close(self) -> None:
        if self.video:
            self.video.close()
            self.video = None
        if self.owns_session and self.serial:
            from .bootstrap_runtime import shutdown
            from .engine_adapter import engine_port
            try:
                shutdown(str(self.adb), self.serial, port=engine_port())
            except (OSError, subprocess.SubprocessError):
                pass  # An exited Android process no longer owns live probe state.
            self.owns_session = False
        if self.process and self.process.poll() is None:
            if self.serial:
                self.command("emu", "kill", timeout=10, check=False)
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        self.process = None
