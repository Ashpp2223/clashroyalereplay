"""Install the pinned probe APK while preserving the emulator's game resources.

Transaction design reference: FirstLight native_runner/offline_install.py
(Apache-2.0). Only the fixed game package in Android user 0 is replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid
import zipfile

from .runtime import MANIFEST, CONTENT_LOCK, PROBE_SHA256, ROOT, android_sdk, sha256
from .paths import RUNTIME_HOME

PACKAGE = MANIFEST["package"]
DATA = f"/data/user/0/{PACKAGE}"
BACKUP_PREFIX = "/data/local/tmp/cr-replay-install-"
LOCATIONS = {"ce": DATA, "de": f"/data/user_de/0/{PACKAGE}",
             "external": f"/sdcard/Android/data/{PACKAGE}", "obb": f"/sdcard/Android/obb/{PACKAGE}"}


class Device:
    def __init__(self, adb: str, serial: str):
        self.adb, self.serial = adb, serial

    def run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run([self.adb, "-s", self.serial, *map(str, args)],
                                capture_output=True, timeout=300,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
        if check and result.returncode:
            raise RuntimeError(output or "ADB 操作失败")
        return output

    def shell(self, script: str, *, check: bool = True) -> str:
        return self.run("shell", script, check=check)

    def check_environment(self, *, root_required: bool = True) -> None:
        if not re.fullmatch(r"(?:emulator-\d+|127\.0\.0\.1:\d+|localhost:\d+)", self.serial):
            raise ValueError("资源保留安装仅支持选定的本机复盘模拟器")
        if (root_required and self.shell("id -u") != "0") or self.shell("am get-current-user") != "0":
            raise ValueError("安装要求 ADB root 和 Android 用户 0")
        if "arm64-v8a" not in self.shell("getprop ro.product.cpu.abilist").split(","):
            raise ValueError("模拟器不支持 ARM64 应用")


def apk_path(device: Device) -> str:
    lines = device.shell(f"pm path {PACKAGE}", check=False).splitlines()
    if len(lines) != 1 or not lines[0].startswith("package:/data/app/") or not lines[0].endswith("/base.apk"):
        raise ValueError("请先在模拟器安装未修改的原版游戏，并完成游戏内资源更新")
    return lines[0].removeprefix("package:")


def file_hash(device: Device, path: str) -> str:
    output = device.shell("sha256sum " + shlex.quote(path))
    digest = output.split()[0] if output.split() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Android 文件校验失败")
    return digest


def resource_hashes(device: Device, directory: str = DATA + "/update") -> dict[str, str]:
    listing = device.shell(f"if [ -d {shlex.quote(directory)} ]; then cd {shlex.quote(directory)} && find . -type f -exec sha256sum {{}} +; fi")
    hashes = {}
    for line in listing.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]) or not fields[1].startswith("./"):
            raise ValueError("游戏更新资源清单无效")
        name = fields[1][2:]
        if not name or name.startswith("/") or ".." in name.split("/") or name in hashes:
            raise ValueError("游戏更新资源路径无效")
        hashes[name] = fields[0]
    return hashes


def base_asset_hashes(device: Device, hashes: dict[str, str], apk: str) -> dict[str, str]:
    """Android uses unchanged APK assets when an update omits redundant copies."""
    if hashes.get("fingerprint.json") not in CONTENT_LOCK["fingerprints"]:
        return {}  # Report the missing/unsupported update before inspecting fallbacks.
    result = {}
    for name in CONTENT_LOCK["apkFallbackFiles"]:
        if name in hashes:
            continue
        output = device.shell(f"unzip -p {shlex.quote(apk)} {shlex.quote('assets/' + name)} | sha256sum")
        digest = output.split()[0] if output.split() else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"APK 内的资源无法校验：{name}")
        result[name] = digest
    return result


def validate_resources(hashes: dict[str, str], base_assets: dict[str, str] | None = None) -> None:
    if hashes.get("fingerprint.json") not in CONTENT_LOCK["fingerprints"]:
        raise ValueError(f"请先打开未修改的原版游戏，完成 {MANIFEST['runtime_content_version']} 游戏内资源更新")
    for name, expected in CONTENT_LOCK["requiredFiles"].items():
        actual = hashes.get(name)
        if actual is None and name in CONTENT_LOCK["apkFallbackFiles"]:
            actual = (base_assets or {}).get(name)
        if actual != expected:
            raise ValueError(f"游戏资源缺失或不匹配：{name}")


def recover_pending_downloads(device: Device, hashes: dict[str, str]) -> dict[str, str]:
    """Publish completed .update files only after checking the trusted manifest."""
    pending = [name for name in hashes if name.endswith(".update")]
    if not pending:
        return hashes
    if hashes.get("fingerprint.json") not in CONTENT_LOCK["fingerprints"]:
        raise ValueError("更新指纹未核验，不能恢复下载临时文件")
    device.shell(f"am force-stop {PACKAGE}")
    fingerprint_path = DATA + "/update/fingerprint.json"
    if file_hash(device, fingerprint_path) != hashes["fingerprint.json"]:
        raise ValueError("更新指纹已变化，停止恢复")
    manifest = json.loads(device.shell("cat " + shlex.quote(fingerprint_path)))
    expected = {row["file"]: row["sha"] for row in manifest["files"]}
    plan = []
    for name in pending:
        target = name.removesuffix(".update")
        if not target or target.startswith("/") or ".." in target.split("/") or "\\" in target:
            raise ValueError("下载临时文件路径无效")
        source = DATA + "/update/" + name
        result = device.shell("sha1sum " + shlex.quote(source)).split()
        if not result or not re.fullmatch(r"[0-9a-f]{40}", result[0]) or result[0] != expected.get(target):
            raise ValueError(f"下载临时文件未完成或不匹配：{name}")
        if target in hashes and hashes[target] != hashes[name]:
            raise ValueError(f"正式资源与下载临时文件冲突：{target}")
        if target not in hashes:
            plan.append((name, target))
    updated = dict(hashes)
    for name, target in plan:
        source, destination = (DATA + "/update/" + item for item in (name, target))
        device.shell(f"cp -p -n {shlex.quote(source)} {shlex.quote(destination)} && restorecon -F {shlex.quote(destination)}")
        if file_hash(device, destination) != hashes[name]:
            raise ValueError(f"恢复后的资源校验失败：{target}")
        updated[target] = hashes[name]
    if plan:
        save_receipt(RUNTIME_HOME / "pending-resource-recovery.json",
                     {"serial": device.serial, "fingerprintSha256": hashes["fingerprint.json"],
                      "restored": [{"source": name, "file": target, "sha256": hashes[name]} for name, target in plan]})
    return updated


def validate_apk(apk: Path, *, update: bool = False) -> dict:
    receipt = json.loads(apk.with_suffix(".build.json").read_text(encoding="utf-8"))
    expected = {"schema": "cr-update-build.v1" if update else "cr-probe-build.v1", "package": PACKAGE,
                "apkSha256": sha256(apk), "libgSha256": MANIFEST["libg_sha256"],
                "resources": "existing-device-update", "bootstrap": "direct-libg-for-update" if update else "explicit-post-libg"}
    if not update:
        expected["probeSha256"] = PROBE_SHA256
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("请用当前准备脚本重新构建带探针的 APK")
    with zipfile.ZipFile(apk) as archive:
        entries = [("lib/arm64-v8a/libg.so", MANIFEST["libg_sha256"])]
        if update:
            if "lib/arm64-v8a/libcrprobe.so" in archive.namelist():
                raise ValueError("资源更新包不能包含探针")
        else:
            entries.append(("lib/arm64-v8a/libcrprobe.so", PROBE_SHA256))
        for name, expected_hash in entries:
            if hashlib.sha256(archive.read(name)).hexdigest() != expected_hash:
                raise ValueError("APK 内的引擎或探针哈希不符")
    return receipt


def save_receipt(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def require_success(output: str) -> None:
    # MuMu's Android 15 installer prepends "Success: streamed ... bytes".
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or lines[-1] != "Success" or any(line.startswith(("Failure", "Error:")) for line in lines):
        raise RuntimeError(output or "Android 安装操作没有报告成功")


def restore_data(device: Device, state: dict) -> None:
    uid = device.shell(f"stat -c %u {DATA}")
    if not uid.isdecimal() or int(uid) < 10000:
        raise ValueError("安装后的游戏 UID 无效")
    for name in state["locations"]:
        destination = LOCATIONS[name]
        source = state["backup"] + "/" + name
        device.shell(f"mkdir -p {shlex.quote(destination)} && cp -a {shlex.quote(source)}/. {shlex.quote(destination)}/")
        if name in ("ce", "de"):
            device.shell(f"chown -R {uid}:{uid} {destination} && restorecon -RF {destination}")


def verify_backup(device: Device, state: dict) -> None:
    if state.get("package") != PACKAGE or state.get("serial") != device.serial:
        raise ValueError("备份所属游戏或模拟器不符")
    if not re.fullmatch(re.escape(BACKUP_PREFIX) + r"[0-9a-f]{32}", state.get("backup", "")):
        raise ValueError("备份目录无效")
    locations = state.get("locations", [])
    if "ce" not in locations or len(set(locations)) != len(locations) or any(name not in LOCATIONS for name in locations):
        raise ValueError("备份的数据目录无效")
    if state.get("target") not in ("original", "update", "probe"):
        raise ValueError("安装记录的目标类型无效")
    if file_hash(device, state["backup"] + "/previous.apk") != state["previousApkSha256"]:
        raise ValueError("备份 APK 校验失败，保留当前安装")
    if resource_hashes(device, state["backup"] + "/ce/update") != state["resourceHashes"]:
        raise ValueError("备份资源校验失败，保留当前安装")
    if state["target"] == "probe":
        validate_resources(state["resourceHashes"], base_asset_hashes(device, state["resourceHashes"], state["backup"] + "/previous.apk"))


def restore(device: Device, state: dict, receipt_path: Path) -> dict:
    device.check_environment()
    verify_backup(device, state)  # Every backup check precedes package removal.
    device.shell(f"am force-stop {PACKAGE}")
    if device.shell(f"pm path {PACKAGE}", check=False).startswith("package:"):
        require_success(device.shell(f"pm uninstall {PACKAGE}"))
    require_success(device.run("shell", f"pm install --user 0 {state['backup']}/previous.apk", check=False))
    restore_data(device, state)
    if resource_hashes(device) != state["resourceHashes"]:
        raise RuntimeError("恢复后的资源与备份不符；备份仍然保留")
    state.update(status="restored")
    state.pop("error", None)
    state.pop("rollbackError", None)
    save_receipt(receipt_path, state)
    return state


def install(device: Device, apk: Path, receipt_path: Path, *, original: bool = False, update: bool = False) -> dict:
    if original and update:
        raise ValueError("只能选择一种安装类型")
    requires_update = not original and not update
    device.check_environment(root_required=requires_update)
    if original:
        with zipfile.ZipFile(apk) as archive:
            if hashlib.sha256(archive.read("lib/arm64-v8a/libg.so")).hexdigest() != MANIFEST["libg_sha256"]:
                raise ValueError("原版 APK 的 libg.so 与固定版本不符")
            if any("libcrprobe.so" in name or "frida-gadget" in name for name in archive.namelist()):
                raise ValueError("首次更新游戏资源必须使用未修改的原版 APK")
        build = {"apkSha256": sha256(apk)}
    else:
        build = validate_apk(apk, update=True) if update else validate_apk(apk)
    installed_path = device.shell(f"pm path {PACKAGE}", check=False)
    if installed_path and not installed_path.startswith("package:"):
        raise RuntimeError("无法确认当前游戏安装状态")
    if not installed_path:
        if requires_update:
            raise ValueError("请先安装未修改的原版游戏，并完成游戏内资源更新")
        require_success(device.run("install", str(apk), check=False))
        if file_hash(device, apk_path(device)) != build["apkSha256"]:
            raise RuntimeError("原版游戏安装后的 APK 校验失败")
        return {"status": "update-installed" if update else "original-installed", "serial": device.serial}
    device.check_environment()  # Replacing an existing installation requires a verified root backup.
    previous = apk_path(device)
    info = device.shell(f"dumpsys package {PACKAGE}")
    if not re.search(r"\bversionName=" + re.escape(MANIFEST["apk_version"]) + r"(?:\s|$)", info):
        raise ValueError("已安装的游戏版本不受固定探针支持")
    if re.findall(r"User (\d+):[^\n]*installed=true", info) != ["0"]:
        raise ValueError("请使用游戏仅安装在 Android 用户 0 的复盘专用实例")
    if file_hash(device, previous.rsplit("/", 1)[0] + "/lib/arm64/libg.so") != MANIFEST["libg_sha256"]:
        raise ValueError("已安装的 libg.so 与固定版本不符")
    before = resource_hashes(device)
    if requires_update:
        validate_resources(before, base_asset_hashes(device, before, previous))
    if file_hash(device, previous) == build["apkSha256"]:
        return {"status": "already-installed", "serial": device.serial}
    device.shell(f"am force-stop {PACKAGE}")
    before = resource_hashes(device)
    if requires_update:
        validate_resources(before, base_asset_hashes(device, before, previous))
    backup = BACKUP_PREFIX + uuid.uuid4().hex
    device.shell(f"mkdir -m 700 {backup} && cp {shlex.quote(previous)} {backup}/previous.apk")
    locations = []
    for name, path in LOCATIONS.items():
        if device.shell(f"if [ -d {path} ]; then echo present; fi") == "present":
            device.shell(f"cp -a {path} {backup}/{name}")
            locations.append(name)
    state = {"schema": "cr-runtime-install.v1", "package": PACKAGE, "serial": device.serial,
             "backup": backup, "locations": locations, "resourceHashes": before,
             "previousApkSha256": file_hash(device, previous), "apkSha256": build["apkSha256"],
             "target": "original" if original else "update" if update else "probe",
             "status": "backed-up"}
    verify_backup(device, state)
    save_receipt(receipt_path, state)
    device.run("push", str(receipt_path), backup + "/receipt.json")
    candidate = backup + "/candidate.apk"
    device.run("push", str(apk), candidate)
    if file_hash(device, candidate) != build["apkSha256"]:
        raise ValueError("上传的 APK 校验失败，保留原版安装")
    try:
        output = device.run("shell", f"pm install --user 0 -r {candidate}", check=False)
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output:
            verify_backup(device, state)
            require_success(device.shell(f"pm uninstall {PACKAGE}"))
            require_success(device.run("shell", f"pm install --user 0 {candidate}", check=False))
            restore_data(device, state)
        else:
            require_success(output)
        if file_hash(device, apk_path(device)) != build["apkSha256"] or resource_hashes(device) != before:
            raise RuntimeError("安装后的 APK 或资源与校验记录不符")
        if requires_update:
            validate_resources(before, base_asset_hashes(device, before, apk_path(device)))
    except (Exception, KeyboardInterrupt) as error:
        try:
            restore(device, state, receipt_path)
        except Exception as rollback_error:
            state.update(status="restore-required", error=str(error), rollbackError=str(rollback_error))
            save_receipt(receipt_path, state)
            raise RuntimeError(f"安装失败，自动恢复未完成；备份已保留，恢复记录：{receipt_path}") from error
        raise RuntimeError("游戏包安装失败，已恢复之前的游戏和资源") from error
    state.update(status="installed", resourceFilesVerified=len(before))
    save_receipt(receipt_path, state)
    return state


def import_export(device: Device, export: Path, apk: Path) -> dict:
    """Provision an empty SDK development device from a verified local export."""
    device.check_environment()
    build = validate_apk(apk)
    receipt = json.loads((export / "export-receipt.json").read_text(encoding="utf-8"))
    data = export / PACKAGE
    update = data / "update"
    local = {path.relative_to(update).as_posix(): sha256(path) for path in update.rglob("*") if path.is_file()}
    if local != receipt["updateHashes"]:
        raise ValueError("导出资源与原始校验记录不符")
    with zipfile.ZipFile(apk) as archive:
        base = {name: hashlib.sha256(archive.read("assets/" + name)).hexdigest()
                for name in CONTENT_LOCK["apkFallbackFiles"] if name not in local}
    validate_resources(local, base)
    existing = device.shell(f"pm path {PACKAGE}", check=False)
    if existing:
        if not existing.startswith("package:") or file_hash(device, apk_path(device)) != build["apkSha256"]:
            raise ValueError("导入仅支持空的开发实例或相同的探针包")
        current = resource_hashes(device)
        if current and current != local:
            raise ValueError("开发实例已有其他游戏数据，未覆盖")
    else:
        require_success(device.run("install", str(apk), check=False))
    device.shell(f"am force-stop {PACKAGE}")
    staging = "/data/local/tmp/cr-replay-import-" + uuid.uuid4().hex
    device.shell(f"mkdir -m 700 {staging}")
    locations = []
    # ART/WebView caches are tied to the source Android version; recreate them.
    for name in ("update", "save", "shared_prefs", "files"):
        if (data / name).is_dir():
            if any(path.is_file() for path in (data / name).rglob("*")):
                device.run("push", str(data / name), staging + "/" + name)
            else:
                device.shell(f"mkdir -p {staging}/{name}")
            locations.append(name)
    if resource_hashes(device, staging + "/update") != local:
        raise ValueError("上传的资源校验失败；本机导出仍然保留")
    uid = device.shell(f"stat -c %u {DATA}")
    if not uid.isdecimal() or int(uid) < 10000:
        raise ValueError("开发实例中的应用 UID 无效")
    for name in locations:
        device.shell(f"mkdir -p {DATA}/{name} && cp -a {staging}/{name}/. {DATA}/{name}/")
    device.shell(f"chown -R {uid}:{uid} {DATA} && restorecon -RF {DATA}")
    imported = resource_hashes(device)
    if imported != local:
        raise ValueError("导入后的资源校验失败；本机导出仍然保留")
    validate_resources(imported, base_asset_hashes(device, imported, apk_path(device)))
    return {"status": "imported", "serial": device.serial, "verifiedUpdateFiles": len(local),
            "fingerprintSha256": imported["fingerprint.json"], "apkSha256": build["apkSha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb")
    parser.add_argument("--serial", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apk", type=Path)
    action.add_argument("--original-apk", type=Path)
    action.add_argument("--update-apk", type=Path)
    action.add_argument("--restore", type=Path)
    action.add_argument("--check", action="store_true")
    action.add_argument("--import-data", type=Path)
    parser.add_argument("--prepared-apk", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    device = Device(args.adb or str(android_sdk() / "platform-tools/adb.exe"), args.serial)
    if args.import_data:
        if not args.prepared_apk:
            parser.error("--import-data requires --prepared-apk")
        result = import_export(device, args.import_data, args.prepared_apk)
    elif args.check:
        device.check_environment()
        hashes = resource_hashes(device)
        base_assets = base_asset_hashes(device, hashes, apk_path(device))
        validate_resources(hashes, base_assets)
        result = {"ready": True, "resourceFilesVerified": len(CONTENT_LOCK["requiredFiles"]),
                  "apkFallbackFiles": list(base_assets), "fingerprintSha256": hashes["fingerprint.json"]}
    elif args.restore:
        result = restore(device, json.loads(args.restore.read_text(encoding="utf-8")), args.restore)
    else:
        receipt = args.receipt or RUNTIME_HOME / ("installed-" + re.sub(r"[^a-zA-Z0-9-]", "_", args.serial) + ".json")
        result = install(device, args.original_apk or args.update_apk or args.apk, receipt,
                         original=args.original_apk is not None, update=args.update_apk is not None)
    print(json.dumps({key: value for key, value in result.items() if key != "resourceHashes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
