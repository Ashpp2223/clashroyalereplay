"""Build the compact replay probe from the vendored sources with official NDK r29."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/clash-royale-battle-engine"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk", type=Path, required=True)
    args = parser.parse_args()
    compiler = args.ndk / "toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
    properties = (args.ndk / "source.properties").read_text(encoding="utf-8")
    if "Pkg.Revision = 29.0.14206865" not in properties:
        raise ValueError("Use the pinned official NDK r29 (29.0.14206865)")
    source = VENDOR / "vendor/firstlight/probe"
    output = VENDOR / "prebuilt/libcrprobe.so"
    temporary = ROOT / ".local-runtime/replay-probe-build/libcrprobe.so"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    flags = ["--target=aarch64-linux-android24", "-DCR_MINIMAL_CAPTURE=1", "-std=c++17", "-O2", "-fPIC",
             "-fvisibility=hidden", "-shared", "-Wl,-z,max-page-size=16384", "-Wl,-soname,libcrprobe.so"]
    inputs = [source / name for name in ("cr_replay_probe.cpp", "native_touch_interceptor.cpp",
                                        "native_call_arm64.S", "remaining_runtime_arm64.S")]
    command = [str(compiler), *flags, *map(str, inputs), "-o", str(temporary), "-llog", "-ldl", "-lz"]
    subprocess.run(command, check=True)
    binary = temporary.read_bytes()
    if binary[:4] != b"\x7fELF" or int.from_bytes(binary[18:20], "little") != 183:
        raise ValueError("Compiler did not produce an ARM64 ELF")
    receipt = {
        "schema": "cr-replay-probe-build.v1", "ndkVersion": "29.0.14206865",
        "compiler": subprocess.check_output([str(compiler), "--version"], text=True, encoding="utf-8").strip(),
        "flags": flags, "linkLibraries": ["log", "dl", "z"],
        "profile": "compact-capture-v3-with-replay-snapshots",
        "sourceHashes": {path.relative_to(VENDOR).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(source.rglob("*")) if path.is_file()},
        "probeSha256": hashlib.sha256(binary).hexdigest(),
    }
    output.write_bytes(binary)
    (output.parent / "replay-probe-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"probeSha256": receipt["probeSha256"], "output": str(output)}))


if __name__ == "__main__":
    main()
