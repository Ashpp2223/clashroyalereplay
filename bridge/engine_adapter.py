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
    if cn_profile_enabled():
        from .cn_engine import new_engine as new_cn_engine
        return new_cn_engine(engine_port())
    from native_engine.engine import RenderedEngine
    return RenderedEngine(port=engine_port())


def engine_port():
    port = int(os.environ.get('CR_ENGINE_PORT', '26791' if cn_profile_enabled() else '26789'))
    if not 1024 <= port <= 65535:
        raise ValueError('战斗接口端口无效')
    return port


def cn_profile_enabled():
    return selected_profile() == 'cn-38.4.5'


def selected_profile():
    profile = os.environ.get('CR_ENGINE_PROFILE', 'legacy')
    if profile not in ('legacy', 'cn-38.4.5', 'clashback-160635001'):
        raise ValueError('未知战斗引擎配置')
    return profile


def new_runtime():
    profile = selected_profile()
    if profile == 'cn-38.4.5':
        from .cn_engine import CampRuntime
        return CampRuntime()
    if profile == 'clashback-160635001':
        from .clashback_engine import ClashbackRuntime
        return ClashbackRuntime()
    from .runtime import AndroidRuntime
    return AndroidRuntime()


def new_play(owner: int, slot: int, x: int, y: int):
    _load_package()
    from native_engine.engine import Play
    return Play(owner=owner, slot=slot, x=x, y=y)
