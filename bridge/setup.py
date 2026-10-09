"""Provision the pinned minimal Android runtime from official archives."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import threading
import urllib.request
import zipfile

from .paths import RUNTIME_HOME

ARCHIVES = (
    ("platform-tools_r37.0.1-win.zip", 8044989, "e03e78b1d80b396f1c3358e31251cb31740e1110", "platform-tools", "platform-tools"),
    ("emulator-windows_x64-15507667.zip", 418510366, "d5f07a99221c6e528a4942887dc36ada7d1602f8", "emulator", "emulator"),
    ("sys-img/google_apis/x86_64-31_r14.zip", 1470822277, "9aedd3e85cad7a479146f6858f4a94840c2a3f29", "x86_64", "system-images/android-31/google_apis/x86_64"),
)
GOOGLE = "https://dl.google.com/android/repository/"


def extract_checked(archive: Path, destination: Path, prefix: str | None = None, limit: int = 12 * 1024**3):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        if sum(item.file_size for item in zipped.infolist()) > limit:
            raise ValueError("资源包展开后过大")
        for item in zipped.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("资源包路径无效")
            if prefix and (not path.parts or path.parts[0] != prefix):
                raise ValueError("官方安装包目录不匹配")
            target = destination.joinpath(*(path.parts[1:] if prefix else path.parts)).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("资源包超出目标目录")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def archive_hash(path):
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_checked(url, path, size, expected, progress):
    if path.exists() and path.stat().st_size == size and archive_hash(path) == expected:
        progress(size)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    with temporary.open("wb") as stream:
        stream.truncate(size)
    block = 8 * 1024**2
    def fetch(start):
        end = min(start + block, size) - 1
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                        raise ValueError("下载服务器未返回有效分段")
                    data = response.read(end - start + 1)
                if len(data) != end - start + 1:
                    raise ValueError("下载中断，请重试")
                with temporary.open("r+b") as stream:
                    stream.seek(start)
                    stream.write(data)
                return len(data)
            except Exception:
                if attempt == 2:
                    raise
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(fetch, start) for start in range(0, size, block)]):
            progress(future.result())
    if archive_hash(temporary) != expected:
        raise ValueError("官方安装包校验失败，未安装")
    temporary.replace(path)


def write_avd(home, sdk, device_home=None):
    name = "CR_Replay_API31"
    directory = (device_home or home) / "avd" / (name + ".avd")
    directory.mkdir(parents=True, exist_ok=True)
    settings = {"avd.id": name, "avd.name": name, "avd.ini.encoding": "UTF-8", "abi.type": "x86_64",
                "image.sysdir.1": str(sdk / "system-images/android-31/google_apis/x86_64").replace("\\", "/") + "/",
                "tag.id": "google_apis", "tag.display": "Google APIs", "PlayStore.enabled": "no",
                "hw.cpu.arch": "x86_64", "hw.cpu.ncore": "2", "hw.ramSize": "2048",
                "hw.lcd.width": "1080", "hw.lcd.height": "1920", "hw.lcd.density": "320", "hw.lcd.depth": "32",
                "hw.initialOrientation": "portrait", "hw.gpu.enabled": "yes", "hw.gpu.mode": "auto",
                "hw.camera.front": "none", "hw.camera.back": "none", "hw.audioInput": "no", "hw.audioOutput": "no",
                "hw.keyboard": "yes", "hw.mainKeys": "no", "disk.dataPartition.size": "10G"}
    (directory / "config.ini").write_text("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n", encoding="utf-8")
    avd_home = directory.parent
    (avd_home / (name + ".ini")).write_text(f"avd.ini.encoding=UTF-8\npath={directory}\ntarget=android-31\n", encoding="utf-8")
    config = {"backend": "android-sdk", "sdkRoot": str(sdk), "avdHome": str(avd_home), "avdName": name, "serial": "emulator-5556", "gpu": "auto"}
    (home / "runtime.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


class SetupManager:
    def __init__(self, home=RUNTIME_HOME):
        self.home = Path(home)
        self.status = {"state": "idle", "stage": "", "downloaded": 0, "total": sum(item[1] for item in ARCHIVES), "error": None}
        self._thread = None

    def start(self, accepted, directory=None):
        if accepted is not True:
            raise ValueError("请先阅读并接受 Android SDK 使用条款")
        if self._thread and self._thread.is_alive():
            return self.status.copy()
        installation = Path(directory).resolve() if directory else self.home
        self._thread = threading.Thread(target=self._install, args=(installation,), daemon=True)
        self._thread.start()
        return self.status.copy()

    def _install(self, installation=None):
        try:
            if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
                raise ValueError("当前安装向导支持 Windows 64 位 Intel/AMD 电脑")
            self.home.mkdir(parents=True, exist_ok=True)
            installation = installation or self.home
            if (self.home / "runtime.json").exists():
                raise ValueError("已有运行环境，请选择已有环境，避免覆盖")
            if not str(installation).isascii():
                raise ValueError("安卓组件不兼容此路径，请点击更改安装位置，选择英文路径后重试")
            installation.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(installation).free < 16 * 1024**3:
                raise ValueError("安装组件、战斗资源和虚拟设备需要至少 16 GB 可用磁盘空间")
            self.status.update(state="installing", downloaded=0, error=None)
            sdk = installation / "sdk"
            for file, size, checksum, prefix, relative in ARCHIVES:
                self.status["stage"] = "下载 Android 运行组件" if prefix != "x86_64" else "下载 Android 系统镜像"
                archive = installation / "downloads" / Path(file).name
                download_checked(GOOGLE + file, archive, size, checksum,
                                 lambda count: self.status.update(downloaded=self.status["downloaded"] + count))
                self.status["stage"] = "校验并安装 Android 运行组件"
                extract_checked(archive, sdk / relative, prefix)
            result = subprocess.run([str(sdk / "emulator/emulator.exe"), "-accel-check"], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError("请启用处理器虚拟化及 Windows Hypervisor Platform，并重启电脑后重试。帮助：https://developer.android.com/studio/run/emulator-acceleration")
            write_avd(self.home, sdk, installation)
            self.status.update(state="ready", stage="安卓环境已安装，请导入战斗资源", error=None)
        except Exception as error:
            self.status.update(state="failed", error=str(error))

    def import_resources(self, archive_path, controller):
        if self._thread and self._thread.is_alive():
            raise ValueError("请等待当前安装完成")
        if not (self.home / "runtime.json").is_file():
            raise ValueError("请先安装安卓运行环境")
        def work():
            try:
                from .runtime import AndroidRuntime, sha256
                from .install_runtime import Device, import_export, validate_apk
                self.status.update(state="importing", stage="校验并导入战斗资源", error=None)
                archive = Path(archive_path)
                digest = sha256(archive)
                staging = self.home / ("resources-" + digest[:16])
                marker = staging / ".extracted"
                if not marker.is_file() or marker.read_text(encoding="utf-8") != digest:
                    if shutil.disk_usage(self.home).free < 3 * 1024**3:
                        raise ValueError("导入战斗资源需要至少 3 GB 可用磁盘空间")
                    extract_checked(archive, staging, limit=2 * 1024**3)
                    marker.write_text(digest, encoding="utf-8")
                manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
                if manifest.get("schema") != "cr-native-resources.v1":
                    raise ValueError("请选择复盘器战斗资源包")
                apk = staging / "prepared.apk"
                receipt = validate_apk(apk)
                controller.close()
                runtime = AndroidRuntime()
                controller.runtime = runtime
                self.status["stage"] = "首次启动安卓环境"
                runtime.serial = runtime._find_arm64_device()
                if not runtime.serial:
                    runtime._launch_sdk_emulator()
                    runtime.serial = runtime._wait_for_avd(180)
                runtime._wait_for_boot(120)
                runtime.command("root", check=False)
                runtime.command("wait-for-device")
                self.status["stage"] = "安装并核验战斗资源"
                import_export(Device(str(runtime.adb), runtime.serial), staging / "export", apk)
                (self.home / "prepared.json").write_text(json.dumps({"path": str(apk), "apkSha256": receipt["apkSha256"]}), encoding="utf-8")
                runtime.apk_sha256 = receipt["apkSha256"]
                controller.last_error = None
                self.status.update(state="ready", stage="战斗资源已导入，点击开始连接", error=None)
            except Exception as error:
                controller.close()
                self.status.update(state="failed", error=str(error))
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        return self.status.copy()
