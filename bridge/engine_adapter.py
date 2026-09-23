"""Load the pinned upstream engine without renaming its vendor directory."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "clash-royale-battle-engine"


def _load_package() -> None:
    if "native_engine" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "native_engine", VENDOR / "__init__.py", submodule_search_locations=[str(VENDOR)])
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载固定版本战斗引擎")
    module = importlib.util.module_from_spec(spec)
    sys.modules["native_engine"] = module
    spec.loader.exec_module(module)


def new_engine():
    _load_package()
    from native_engine.engine import RenderedEngine
    return RenderedEngine()


def new_play(owner: int, slot: int, x: int, y: int):
    _load_package()
    from native_engine.engine import Play
    return Play(owner=owner, slot=slot, x=x, y=y)
