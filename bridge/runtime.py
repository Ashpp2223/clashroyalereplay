"""Manage the development Android environment and attest the pinned battle engine."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
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


def rgba_png(raw: bytes, *, compression: int = 1) -> bytes:
    """Encode screencap pixels losslessly without launching a per-frame process."""
    width, height, pixel_format = struct.unpack_from("<III", raw)
    header = len(raw) - width * height * 4
    if pixel_format != 1 or not (0 < width <= 8192 and 0 < height <= 8192) or header not in (12, 16):
        raise ValueError("不支持的原生截图格式")
    stride = width * 4
    pixels = memoryview(raw)[header:]
    rows = bytearray((stride + 1) * height)
    for y in range(height):
        # PNG filter 0 keeps all RGBA channels intact, including alpha.
        rows[y * (stride + 1) + 1:(y + 1) * (stride + 1)] = pixels[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, compression)) + chunk(b"IEND", b""))


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
        self.owns_session = False
        self.runtime_fingerprint_sha256 = MANIFEST["runtime_fingerprint_sha256"]
        self._verified_apk: Path | None = None
        self._apk_cache: dict | None = None
        self._ready_for_snapshot = False
        self.apk_cache_hit = False
        self.resumed_game = False
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
        self._verified_apk = None
        self._ready_for_snapshot = False
        self.resumed_game = False
        self.apk_cache_hit = False
        self._load_apk_cache()
        if not self.adb.is_file():
            raise RuntimeError("未找到 ADB；请安装 Android platform-tools 或设置 CR_ADB")
        # Hash the local APK in the background startup, after the HTTP greeting.
        prepared = RUNTIME_HOME / "prepared.json"
        if prepared.is_file():
            payload = json.loads(prepared.read_text(encoding="utf-8-sig"))
            path = Path(payload.get("path", ""))
            if not path.is_file() or sha256(path) != self.apk_sha256:
                raise RuntimeError("本地准备的 APK 缺失或哈希不符")
            self._verified_apk = path
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
        self._check_installed_runtime()
        self.stage = "启动原生探针"
        self.stage_code = "probe"
        self.owns_session = True
        from .bootstrap_runtime import bootstrap
        from .engine_adapter import engine_port
        resumed = bootstrap(str(self.adb), self.serial, port=engine_port(), timeout=90, verified=True,
                            resume=bool(self.apk_cache_hit and self.process and getattr(self, '_using_quick_boot', False)))
        self.resumed_game = bool(resumed.get('resumed'))
        self.stage = "读取能力声明"
        self.stage_code = "capabilities"
        attest = self._attest()
        self._ready_for_snapshot = True
        self._save_apk_cache()
        self.video = NativeFrameStream(str(self.adb), self.serial)
        self.video.start()
        self.stage = "就绪"
        self.stage_code = "ready"
        self.error = None
        return attest

    def _load_apk_cache(self) -> None:
        # Unchanged APK/library files reuse their completed installation scan.
        # Update assets and live probe attestation are still hashed every time.
        identity = hashlib.sha256(json.dumps({
            'apk': self.apk_sha256, 'probe': PROBE_SHA256, 'libg': MANIFEST['libg_sha256'],
            'content': CONTENT_LOCK, 'config': self.config, 'serial': self.preferred_serial,
        }, sort_keys=True).encode()).hexdigest()
        record = {'schema': 'cr-apk-verification.v1', 'identity': identity}
        try:
            saved = json.loads((RUNTIME_HOME / 'apk-verification.json').read_text(encoding='utf-8'))
            if isinstance(saved, dict) and all(saved.get(key) == value for key, value in record.items()):
                record = saved
        except (OSError, ValueError):
            pass
        self._apk_cache = record

    def _installed_apk_stamp(self, path: str) -> dict | None:
        if getattr(self, '_apk_cache', None) is None or self.config.get('backend') != 'android-sdk':
            return None
        try:
            directory = path.rsplit('/', 1)[0]
            result = self.command('shell', 'stat', '-c', '%d:%i:%s:%y:%z', path,
                                  directory + '/lib/arm64/libg.so', directory + '/lib/arm64/libcrprobe.so', check=False)
            value = result.stdout.strip()
            lines = value.splitlines()
            if result.returncode == 0 and len(lines) == 3 and all(re.fullmatch(r'\d+:\d+:\d+:[^\r\n]+', line) for line in lines):
                return {'serial': self.serial, 'path': path, 'stat': value}
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _save_apk_cache(self) -> None:
        try:
            RUNTIME_HOME.mkdir(parents=True, exist_ok=True)
            target = RUNTIME_HOME / 'apk-verification.json'
            temporary = target.with_suffix('.partial')
            temporary.write_text(json.dumps(self._apk_cache), encoding='utf-8')
            temporary.replace(target)
        except OSError:
            pass  # An unwritable cache only means doing the checks next time.

    def _launch_sdk_emulator(self, *, cold_boot: bool = False) -> None:
        if not self.preferred_serial.startswith("emulator-") or not self.emulator.is_file():
            raise RuntimeError("未找到选定的 Android 设备或官方开发模拟器")
        port = int(self.preferred_serial.removeprefix("emulator-"))
        if port < 5554 or port > 5682 or port % 2:
            raise RuntimeError("开发模拟器端口必须为 5554～5682 的偶数")
        avd_home = Path(self.config.get("avdHome", str(RUNTIME_HOME / "avd")))
        if not (avd_home / (self.avd_name + ".ini")).is_file():
            raise RuntimeError("尚未连接战斗运行环境，请点击“设置运行环境”选择已有环境目录")
        snapshot = avd_home / (self.avd_name + '.avd') / 'snapshots/default_boot/snapshot.pb'
        self._using_quick_boot = not cold_boot and snapshot.is_file()
        self.stage = "快速启动未完成，正在完整启动 Android" if cold_boot else "启动 Android 开发环境"
        self.stage_code = "emulator-cold" if cold_boot else "emulator"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = RUNTIME_HOME / "dev-emulator.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as output:
            self.process = subprocess.Popen(
                [str(self.emulator), "-avd", self.avd_name, "-port", str(port), "-no-window", "-noaudio",
                 "-no-metrics", "-gpu", self.config.get("gpu", "host"), "-memory", "2048", "-cores", "2",
                 *(['-no-snapshot-load'] if cold_boot else [])],
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
        try:
            listing = subprocess.run([str(self.adb), "devices"], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if listing.returncode:
            return None  # ADB can briefly restart while Quick Boot reconnects.
        devices = listing.stdout
        preferred = self.preferred_serial
        for line in devices.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device" or (preferred and parts[0] != preferred):
                continue
            try:
                result = subprocess.run([str(self.adb), "-s", parts[0], "shell", "getprop", "ro.product.cpu.abilist"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and "arm64-v8a" in result.stdout.strip().split(","):
                return parts[0]
        return None

    def _wait_for_avd(self, timeout: float) -> str:
        started = time.monotonic()
        deadline = started + timeout
        retried = False
        while time.monotonic() < deadline:
            self._connect_selected_device()
            serial = self._find_arm64_device()
            if serial:
                return serial
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"开发模拟器启动失败（{self.process.returncode}）；详情见 .local-runtime/dev-emulator.log")
            if (self.process and getattr(self, '_using_quick_boot', False) and not retried and
                    time.monotonic() - started >= 45):
                # Only restart the emulator this runtime launched. Existing
                # user-owned devices and first-ever cold boots keep their budget.
                self._stop_owned_emulator(timeout=5)
                self._launch_sdk_emulator(cold_boot=True)
                retried = True
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
        stamp = self._installed_apk_stamp(base_apk)
        cached = stamp is not None and self._apk_cache.get('installed') == stamp
        if not cached and self.command("shell", "sha256sum", directory + "/lib/arm64/libg.so").stdout.split()[0] != MANIFEST["libg_sha256"]:
            raise RuntimeError("已安装的 libg.so 与固定引擎版本不符")
        self.stage = "校验游戏资源"
        self.stage_code = "resources"
        # The first scan overlaps APK hashing and resource verification. Later
        # scans reuse only the unchanged installed APK; update files stay live.
        with ThreadPoolExecutor(max_workers=1) as verification:
            apk_check = None if cached else verification.submit(self.command, "shell", "sha256sum", base_apk, timeout=120)
            self._check_updated_content()
            installed = cached or apk_check.result().stdout.split()[0] == self.apk_sha256
        if not installed:
            self._install_local_prepared()
            base_apk = self.command("shell", "pm", "path", package).stdout.strip().removeprefix("package:")
            directory = base_apk.rsplit("/", 1)[0]
            stamp = self._installed_apk_stamp(base_apk)
        checks = {
            "APK": (base_apk, self.apk_sha256),
            "libg": (directory + "/lib/arm64/libg.so", MANIFEST["libg_sha256"]),
            "探针": (directory + "/lib/arm64/libcrprobe.so", PROBE_SHA256),
        }
        for label, (path, expected) in checks.items():
            # APK and libg were already checked above. Installing a replacement
            # invalidates those results, so all three are then verified again.
            if cached or (installed and label in ("APK", "libg")):
                continue
            result = self.command("shell", "sha256sum", path, timeout=120 if label == "APK" else 30, check=False)
            actual = result.stdout.split()[0].lower() if result.stdout.split() else ""
            if actual != expected:
                raise RuntimeError(f"{label} 哈希不符或文件缺失：{actual or '未找到'}")
        if stamp is not None:
            if self._installed_apk_stamp(base_apk) != stamp:
                raise RuntimeError('已安装的 APK 在校验期间发生变化')
            self._apk_cache['installed'] = stamp
            self.apk_cache_hit = cached
        if (not cached and getattr(self, 'serial', '').startswith('emulator-') and
                getattr(self, 'config', {}).get('backend') == 'android-sdk'):
            # This initial Android setting persists with the device; do not
            # repeat setup writes during every subsequent connection.
            self.command('shell', 'settings', '--user', '0', 'put', 'secure', 'immersive_mode_confirmations', 'confirmed')

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
            # The installed APK must have a completed pinned hash check for its
            # current file identity. Verified bytes on the host avoid four
            # guest unzip scans of the ~800 MB archive for tiny fallback assets.
            source = getattr(self, '_verified_apk', None) or apk_path(device)
            validate_resources(hashes, base_asset_hashes(device, hashes, source))
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
        # Loopback ADB transfers raw pixels faster than compressing them inside
        # Android. PNG level 0 also avoids recompressing the same fresh pixels.
        try:
            result = subprocess.run(
                [str(self.adb), "-s", self.serial, "exec-out", "screencap"],
                capture_output=True, timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return rgba_png(result.stdout, compression=0)
        except (OSError, ValueError, EOFError, struct.error, zlib.error, subprocess.SubprocessError, ImportError, RuntimeError):
            # Older devices retain Android's PNG capture as a fallback.
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
            from .engine_adapter import engine_port, new_engine
            preserve_game = False
            if getattr(self, '_ready_for_snapshot', False) and self.process and self.process.poll() is None:
                engine = new_engine()
                engine.timeout = 3
                try:
                    paused = engine.request('pause')
                    cleared = engine.request('replay-schedule-clear')
                    preserve_game = bool(paused.get('ok') and cleared.get('ok'))
                except (OSError, ValueError, RuntimeError):
                    pass
                finally:
                    engine.close()
            try:
                shutdown(str(self.adb), self.serial, port=engine_port(), preserve_game=preserve_game)
            except (OSError, subprocess.SubprocessError):
                pass  # An exited Android process no longer owns live probe state.
            self.owns_session = False
        self._ready_for_snapshot = False
        self._stop_owned_emulator()

    def _stop_owned_emulator(self, *, timeout: float = 45) -> None:
        process = self.process
        if process and process.poll() is None:
            # A failed Quick Boot may never have set self.serial. Its owned
            # emulator console can still be addressed using the requested serial.
            try:
                subprocess.run([str(self.adb), '-s', self.serial or self.preferred_serial, 'emu', 'kill'],
                               capture_output=True, timeout=5, check=False,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    # emulator.exe owns a QEMU child; terminating only its
                    # launcher leaves an offline device that prevents retries.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=5, check=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                else:
                    process.terminate()
                process.wait(timeout=5)
        self.process = None
