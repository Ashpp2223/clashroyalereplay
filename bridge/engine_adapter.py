"""Load the pinned upstream engine without renaming its vendor directory."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import os
from .paths import VENDOR


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
    return RenderedEngine(port=engine_port())


def engine_port():
    port = int(os.environ.get('CR_ENGINE_PORT', '26789'))
    if not 1024 <= port <= 65535:
        raise ValueError('战斗接口端口无效')
    return port


def new_play(owner: int, slot: int, x: int, y: int):
    _load_package()
    from native_engine.engine import Play
    return Play(owner=owner, slot=slot, x=x, y=y)
