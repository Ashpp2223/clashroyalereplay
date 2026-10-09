"""Validate and patch the pinned APK's Java bootstrap for the existing probe.

Loading order reference (Apache-2.0): FirstLight native_runner/offline_build.py.
https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/main/native_runner/offline_build.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from bridge.runtime import MANIFEST, PROBE_SHA256, sha256


def validate_payload(apk: Path, *, prepared: bool = False) -> dict:
    with zipfile.ZipFile(apk) as archive:
        game_hash = hashlib.sha256(archive.read("lib/arm64-v8a/libg.so")).hexdigest()
        if game_hash != MANIFEST["libg_sha256"]:
            raise ValueError("APK 的 libg.so 与固定引擎版本不符")
        if prepared:
            probe_hash = hashlib.sha256(archive.read("lib/arm64-v8a/libcrprobe.so")).hexdigest()
            if probe_hash != PROBE_SHA256:
                raise ValueError("APK 的探针与固定版本不符")
        if any("frida-gadget" in name for name in archive.namelist()):
            raise ValueError("请使用未注入 Frida Gadget 的原版 APK")
        if not prepared and "lib/arm64-v8a/libcrprobe.so" in archive.namelist():
            raise ValueError("资源更新包不能包含探针")
    return {"apkSha256": sha256(apk), "libgSha256": game_hash}


def patch_bootstrap(source: Path, *, update_only: bool = False) -> None:
    game = source / "smali/com/supercell/clashroyale/GameApp.smali"
    titan = source / "smali/com/supercell/titan/GameApp.smali"
    game_text = game.read_text(encoding="utf-8")
    titan_text = titan.read_text(encoding="utf-8")
    if '"crprobe"' in game_text or "nativeBootstrapProbe" in titan_text or '"frida-gadget"' in game_text or "# CR replay bootstrap" in game_text:
        raise ValueError("解包目录已经注入组件；请从原版 APK 重新解包")
    gate = re.compile(r"(\.method public final a\(\)Z\s+\.locals 3\s*\n)")
    if len(gate.findall(game_text)) != 1:
        raise ValueError("GameApp 的加载入口发生变化")
    probe_load = ('    const-string v0, "crprobe"\n'
                  '    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n')
    game_text = gate.sub(lambda match: match[1] + '\n    # CR replay bootstrap\n' +
                        ("" if update_only else probe_load) + '    const/4 v0, 0x0\n    return v0\n\n', game_text, count=1)
    load_game = re.compile(
        r'(    const-string v3, "g"\s+(?:\.line \d+\s+)*'
        r'invoke-static \{v3\}, Ljava/lang/System;->loadLibrary\(Ljava/lang/String;\)V)'
    )
    if len(load_game.findall(titan_text)) != 1 or titan_text.count("# direct methods") != 1:
        raise ValueError("Titan 的原生库加载顺序发生变化")
    titan_text = load_game.sub(lambda match: (
        '    const-string v3, "scid_sdk"\n'
        '    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n\n'
        + match[1]
        + ("" if update_only else '\n\n    const-string v3, "crprobe"\n'
        '    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
        '    invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V')
    ), titan_text, count=1)
    if not update_only:
        titan_text = titan_text.replace(
            "# direct methods", "# direct methods\n.method private static native nativeBootstrapProbe()V\n.end method\n", 1
        )
    # Both version-specific anchors must pass before either file is written.
    game.write_text(game_text, encoding="utf-8")
    titan.write_text(titan_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-input")
    check.add_argument("--apk", type=Path, required=True)
    patch = commands.add_parser("patch")
    patch.add_argument("--source", type=Path, required=True)
    patch.add_argument("--update-only", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--apk", type=Path, required=True)
    verify.add_argument("--original-apk", type=Path, required=True)
    verify.add_argument("--update-only", action="store_true")
    args = parser.parse_args()
    if args.command == "patch":
        patch_bootstrap(args.source, update_only=args.update_only)
        return
    update_only = getattr(args, "update_only", False)
    receipt = validate_payload(args.apk, prepared=args.command == "verify" and not update_only)
    if args.command == "verify":
        original = validate_payload(args.original_apk)
        with zipfile.ZipFile(args.original_apk) as source, zipfile.ZipFile(args.apk) as output:
            for name in source.namelist():
                if name.startswith("assets/") and not name.endswith("/"):
                    if hashlib.sha256(source.read(name)).digest() != hashlib.sha256(output.read(name)).digest():
                        raise ValueError(f"构建修改了游戏资源：{name}")
        receipt.update(schema="cr-update-build.v1" if update_only else "cr-probe-build.v1", package=MANIFEST["package"],
                       originalApkSha256=original["apkSha256"], resources="existing-device-update",
                       bootstrap="direct-libg-for-update" if update_only else "explicit-post-libg")
        if not update_only:
            receipt["probeSha256"] = PROBE_SHA256
        args.apk.with_suffix(".build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
