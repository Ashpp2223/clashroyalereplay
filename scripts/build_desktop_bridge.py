"""Freeze the one desktop bridge without distributing the user's game assets."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/clash-royale-battle-engine"


def main():
    output = ROOT / "src-tauri/resources"
    work = ROOT / ".local-runtime/package-build"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--noupx",
               "--name", "native-bridge", "--distpath", str(output), "--workpath", str(work),
               "--specpath", str(work), "--paths", str(ROOT), "--collect-all", "imageio_ffmpeg"]
    data = [(ROOT / "bridge/content_lock.json", "bridge"),
            (ROOT / "src/data/nativeCards.json", "src/data"),
            (VENDOR / "standard_match.json", "vendor/clash-royale-battle-engine"),
            (VENDOR / "vendor/firstlight/supported_engine.json", "vendor/clash-royale-battle-engine/vendor/firstlight"),
            (VENDOR / "prebuilt/libcrprobe.so", "vendor/clash-royale-battle-engine/prebuilt"),
            (VENDOR / "vendor/firstlight/LICENSE", "licenses/firstlight"),
            (VENDOR / "vendor/firstlight/NOTICE", "licenses/firstlight")]
    data += [(file, "vendor/clash-royale-battle-engine") for file in VENDOR.glob("*.py")]
    for file, destination in data:
        command.extend(["--add-data", f"{file}:{destination}"])
    command.append(str(ROOT / "bridge/entrypoint.py"))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
