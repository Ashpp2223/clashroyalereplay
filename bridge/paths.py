"""Read-only application resources and writable per-user runtime configuration."""
from pathlib import Path
import os
import sys

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
VENDOR = ROOT / "vendor/clash-royale-battle-engine"
RUNTIME_HOME = Path(os.environ.get("CR_RUNTIME_HOME") or (
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "local.clash-replay-tree/runtime")
    if getattr(sys, "frozen", False) else str(ROOT / ".local-runtime")))
