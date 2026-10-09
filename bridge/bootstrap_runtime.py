"""Bootstrap the pinned probe on native ARM64 or ARM-translating Android hosts."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import time

from .runtime import DEVICE_PORT, ROOT, VENDOR, android_sdk

RULE_COMMENT = "cr-replay-offline"


def cleanup_command(adb: str, serial: str, *args: str):
    try:
        return subprocess.run([adb, "-s", serial, *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=5, check=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


def upstream_bootstrap():
    path = VENDOR / "bootstrap_emulator.py"
    spec = importlib.util.spec_from_file_location("pinned_engine_bootstrap", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"不能加载上游启动脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # ADB and Android emit UTF-8 even on Chinese Windows installations.
    def command(adb, serial, *args, check=True):
        result = subprocess.run([adb, "-s", serial, *map(str, args)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ADB 命令失败")
        return result
    module.command = command
    return module


def clear_offline_rules(upstream, adb: str, serial: str) -> None:
    for table in ("iptables", "ip6tables"):
        listing = cleanup_command(adb, serial, "shell", table, "-L", "OUTPUT", "--line-numbers", "-n")
        if listing is None or listing.returncode:
            continue
        for number in reversed([int(line.split()[0]) for line in listing.stdout.splitlines()
                                if RULE_COMMENT in line and line.split() and line.split()[0].isdigit()]):
            cleanup_command(adb, serial, "shell", table, "-D", "OUTPUT", number)


def shutdown(adb: str, serial: str, port: int = DEVICE_PORT) -> None:
    upstream = upstream_bootstrap()
    cleanup_command(adb, serial, "shell", "am", "force-stop", upstream.PACKAGE)
    clear_offline_rules(upstream, adb, serial)
    result = cleanup_command(adb, serial, "forward", "--list")
    forwards = result.stdout.splitlines() if result is not None else []
    if f"{serial} tcp:{port} tcp:{DEVICE_PORT}" in forwards:
        cleanup_command(adb, serial, "forward", "--remove", f"tcp:{port}")


def wait_until_ready(upstream, adb: str, serial: str, port: int, timeout: float) -> dict:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            status = upstream.read_status(port)
            if status.get("contentReady"):
                return status
        except (ConnectionError, OSError, ValueError):
            pass
        # Allow activity startup, then distinguish a dead game from slow content loading.
        if time.monotonic() - started >= 5:
            process = upstream.shell(adb, serial, "pidof", upstream.PACKAGE, check=False)
            if process.returncode == 1 and not process.stdout.strip():
                crash = cleanup_command(adb, serial, "logcat", "-d", "-b", "crash", "-t", "100")
                if crash is not None:
                    path = ROOT / ".local-runtime/native-startup-crash.log"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(crash.stdout, encoding="utf-8")
                raise RuntimeError("原生游戏进程已退出，未进入战斗；启动记录见 .local-runtime/native-startup-crash.log")
        time.sleep(0.5)
    raise RuntimeError("原生引擎未在规定时间内完成内容加载，尚不能开始复盘")


def bootstrap(adb: str, serial: str, port: int = DEVICE_PORT, timeout: float = 90) -> dict:
    upstream = upstream_bootstrap()
    upstream.command(adb, serial, "root")
    upstream.command(adb, serial, "wait-for-device")
    abi_list = upstream.shell(adb, serial, "getprop", "ro.product.cpu.abilist").stdout.strip().split(",")
    if "arm64-v8a" not in abi_list:
        raise RuntimeError(f"运行时不支持 ARM64 应用：{abi_list}")
    directory = upstream.app_directory(adb, serial)
    upstream.verify_engine(adb, serial, directory, VENDOR)
    uid = upstream.shell(adb, serial, "stat", "-c", "%u", f"/data/user/0/{upstream.PACKAGE}").stdout.strip()
    if not re.fullmatch(r"\d+", uid) or int(uid) < 10000:
        raise RuntimeError("游戏 UID 无效，未修改网络规则")
    forwards = upstream.command(adb, serial, "forward", "--list").stdout.splitlines()
    if any(len(line.split()) >= 3 and line.split()[1] == f"tcp:{port}" and
           line != f"{serial} tcp:{port} tcp:{DEVICE_PORT}" for line in forwards):
        raise RuntimeError("战斗接口端口已被其他设备占用")
    try:
        upstream.shell(adb, serial, "am", "force-stop", upstream.PACKAGE)
        clear_offline_rules(upstream, adb, serial)
        for table in ("iptables", "ip6tables"):
            upstream.shell(adb, serial, table, "-I", "OUTPUT", "!", "-o", "lo", "-m", "owner",
                           "--uid-owner", uid, "-m", "comment", "--comment", RULE_COMMENT, "-j", "REJECT")
        upstream.command(adb, serial, "forward", f"tcp:{port}", f"tcp:{DEVICE_PORT}")
        upstream.shell(adb, serial, "am", "start", "-n", upstream.ACTIVITY)
        return wait_until_ready(upstream, adb, serial, port, timeout)
    except Exception:
        shutdown(adb, serial, port)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--adb")
    args = parser.parse_args()
    adb = args.adb or os.environ.get("CR_ADB") or str(android_sdk() / "platform-tools" / "adb.exe")
    try:
        status = bootstrap(adb, args.serial, timeout=args.timeout)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Battle engine ready at 127.0.0.1:{DEVICE_PORT}; contentReady={status.get('contentReady')}")


if __name__ == "__main__":
    main()
