"""Execute three small, real libg.so ARM64 functions in a Windows process.

This is an isolated feasibility probe, not a battle host: it applies relative
and named relocations, traps unavailable imports, and supplies synthetic
structures to the functions it calls. A local APK libc++_shared.so can be
mapped as a real symbol provider. It can optionally attempt a short prefix of
constructors to diagnose the next missing import. No Android process or
system API is used.

Install the experimental CPU executor locally with::

    python -m pip install --target .local-runtime/win-host-deps-rebuild unicorn==2.1.4
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import threading
import zipfile

import lief


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local-runtime/win-host-deps-rebuild"))
try:
    from unicorn import (Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                         UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE, UC_PROT_READ,
                         UC_PROT_WRITE, UC_PROT_EXEC)
    from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_TPIDR_EL0,
                                     UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                     UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                     UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_X8,
                                     UC_ARM64_REG_X30, UC_ARM64_REG_S0, UC_ARM64_REG_S1,
                                     UC_ARM64_REG_D0)
except ImportError as error:
    raise SystemExit("install Unicorn 2.1.4 under .local-runtime/win-host-deps-rebuild first") from error


MANIFEST = ROOT / "vendor/clash-royale-battle-engine/vendor/firstlight/supported_engine.json"
BASE = 0x40000000
CPP_BASE = 0x50000000
DATA = 0x60000000
STACK = 0x60F00000
STACK_SIZE = 0x104000  # Keep the original 0x61004000 top while allowing 1 MiB of downward growth.
RETURN = 0x62000000
STUB = 0x63000000
DL_ITERATE_PHDR = 0x64000000
DL_ITERATE_CONTINUE = DL_ITERATE_PHDR + 0x10
PTHREAD_ONCE_RETURN = DL_ITERATE_PHDR + 0x20
BSEARCH_CONTINUE = DL_ITERATE_PHDR + 0x30
JNI_BRIDGE = 0x65000000
JNI_VM = JNI_BRIDGE
JNI_ENV = JNI_BRIDGE + 0x100
WORKER_JNI_ENV = JNI_BRIDGE + 0x108
JNI_VM_TABLE = JNI_BRIDGE + 0x200
JNI_ENV_TABLE = JNI_BRIDGE + 0x400
JNI_CLASS_HANDLES = JNI_BRIDGE + 0x1000
JNI_ENV_STUBS = JNI_BRIDGE + 0x2000
JNI_VM_STUBS = JNI_BRIDGE + 0x3000
JNI_TABLE_SLOTS = 256
JNI_TLS = 0x66000000
JNI_TLS_CANARY = 0x4A4E495F43414E59
WORKER_TLS = JNI_TLS + 0x1000
WORKER_STACK = 0x67000000
WORKER_STACK_SIZE = 0x100000
WORKER_HANDLE = WORKER_TLS + 0x100
POOL_WORKER_TLS_BASE = JNI_TLS + 0x2000
POOL_WORKER_STACK_BASE = WORKER_STACK + WORKER_STACK_SIZE
POOL_WORKER_HANDLE_BASE = POOL_WORKER_TLS_BASE + 0x100
JNI_OPAQUE_ASSET_MANAGER = 0x68000010
JNI_OPAQUE_ACTIVITY = 0x68000020
JNI_OPAQUE_DATA_DIR = 0x68000030
JNI_OPAQUE_CACHE_DIR = 0x68000040
JNI_OPAQUE_UNKNOWN = 0x68000050
JNI_OPAQUE_PACKAGE_MANAGER = 0x68000060
JNI_OPAQUE_HPE_FEATURE = 0x68000070
JNI_OPAQUE_MAIN_RESULT = 0x68000080
JNI_OPAQUE_ASSET_MANAGER_LOCAL = 0x68000090
JNI_OPAQUE_APP_VERSION = 0x680000A0
JNI_OPAQUE_LAUNCH_INTENT = 0x680000B0
JNI_OPAQUE_USER_ID_EXTRA = 0x680000C0
JNI_OPAQUE_SURFACE = 0x680000D0
JNI_OPAQUE_WINDOW_SERVICE_NAME = 0x680000E0
JNI_OPAQUE_WINDOW_MANAGER = 0x680000F0
JNI_OPAQUE_DISPLAY = 0x680001C0
VIRTUAL_NATIVE_WINDOW = 0x68000400
VIRTUAL_EGL_DISPLAY = 0x68000600
VIRTUAL_EGL_CONFIG = 0x68000610
VIRTUAL_EGL_CONTEXT = 0x68000620
VIRTUAL_EGL_SURFACE = 0x68000630
GL_STUB_BASE = 0x69000000
GL_STUB_SIZE = 0x10000
GL_VERSION_STRING = 0x69100000
GL_EXTENSION_STRING = GL_VERSION_STRING + 64
GL_RENDERER_STRING = GL_VERSION_STRING + 128
GL_VENDOR_STRING = GL_VERSION_STRING + 192
GL_SHADING_LANGUAGE_STRING = GL_VERSION_STRING + 256
JNI_EMULATOR_METHOD_ID = 0x68000100
JNI_CONTEXT_GET_PM_METHOD_ID = 0x68000110
JNI_PM_HAS_FEATURE_METHOD_ID = 0x68000120
JNI_CONTEXT_GET_ASSETS_METHOD_ID = 0x68000130
JNI_ASSET_MANAGER_LIST_METHOD_ID = 0x68000140
JNI_APP_VERSION_METHOD_ID = 0x68000150
JNI_ACTIVITY_GET_INTENT_METHOD_ID = 0x68000160
JNI_INTENT_HAS_EXTRA_METHOD_ID = 0x68000170
JNI_INTENT_GET_FLAGS_METHOD_ID = 0x68000180
JNI_GAME_APP_GET_INSTANCE_METHOD_ID = 0x68000190
JNI_CONTEXT_GET_SYSTEM_SERVICE_METHOD_ID = 0x680001A0
JNI_WINDOW_MANAGER_GET_DISPLAY_METHOD_ID = 0x680001B0
JNI_DISPLAY_GET_REFRESH_RATE_METHOD_ID = 0x680001D0
JNI_APP_KEYVALUE_METHOD_ID = 0x680001E0
JNI_APP_STORE_KEYVALUE_METHOD_ID = 0x680001F0
VIRTUAL_PROC_FILE = 0x68000200
VIRTUAL_WHICH_SU_PIPE = 0x68000210
VIRTUAL_ASSET_MANAGER = 0x68000300
VIRTUAL_ASSET_FIRST = 0x68000800
HEAP = 0x70000000
HEAP_SIZE = 256 * 1024 * 1024  # Bounded diagnostic arena; free/reuse still needs a real host allocator.
GUEST_THREAD_ID = 1000
GUEST_PTHREAD_SELF = JNI_TLS + 0x100
VIRTUAL_MONOTONIC_NS = 1_000_000_000_000
VIRTUAL_WALL_SECONDS = 1_700_000_000
PAGE = 0x1000
R_AARCH64_RELATIVE = 1027
R_AARCH64_ABS64 = 257
R_AARCH64_GLOB_DAT = 1025
R_AARCH64_JUMP_SLOT = 1026
GAME_STATE_TICK = 0x01062BC0
GAME_STATE_CONTEXT_A = 0x0080031C
GAME_STATE_CONTEXT_B = 0x00EAF1C8
CONTEXT_ROOT_SLOT = 0x019AD878
LIBG_PHDR_CALLBACK = BASE + 0x013D1E3C
LIBG_MPROTECT_WRITABLE = BASE + 0x013D1F28
LIBG_MPROTECT_RESTORE = BASE + 0x013D1F70
JNI_ONLOAD = 0x013CFE74
APPLICATION_SINGLETON_SLOT = 0x01A06AB8
APPLICATION_FLAGS_SLOT = 0x018EEE3C
LIBG_CMDLINE_FOPEN_CALL = BASE + 0x013D01C4
LIBG_CMDLINE_FGETS_CALL = BASE + 0x013D01DC
LIBG_CMDLINE_FCLOSE_CALL = BASE + 0x013D01E8
LIBG_WHICH_SU_POPEN_CALL = BASE + 0x013D05B0
LIBG_WHICH_SU_FGETS_CALL = BASE + 0x013D05C8
LIBG_WHICH_SU_PCLOSE_CALL = BASE + 0x013D05D8
LIBG_XBIN_WHICH_SU_POPEN_CALL = BASE + 0x013D0654
LIBG_XBIN_WHICH_SU_FGETS_CALL = BASE + 0x013D066C
LIBG_XBIN_WHICH_SU_PCLOSE_CALL = BASE + 0x013D067C
LIBG_ASSET_MANAGER_FROM_JAVA_CALL = BASE + 0x013D0324
LIBG_SENTRY_CLEAR_CALL = BASE + 0x013A0FC4
LIBG_SENTRY_SET_TAG_CALL = BASE + 0x013A0FF0


@dataclass
class GuestMutex:
    host_lock: threading.Lock | threading.RLock
    recursive: bool
    owner_tid: int | None = None
    depth: int = 0


def round_up(value: int) -> int:
    return (value + PAGE - 1) & ~(PAGE - 1)


def read_elf_image(path: Path) -> tuple[lief.ELF.Binary, bytearray]:
    elf = lief.parse(path.read_bytes())
    if not isinstance(elf, lief.ELF.Binary) or elf.header.machine_type != lief.ELF.ARCH.AARCH64:
        raise ValueError(f"expected an AArch64 ELF: {path}")
    segments = [item for item in elf.segments if item.type == lief.ELF.SEGMENT_TYPES.LOAD]
    if not segments:
        raise ValueError(f"no PT_LOAD segments: {path}")
    image_size = round_up(max(item.virtual_address + item.virtual_size for item in segments))
    image = bytearray(image_size)
    with path.open("rb") as source:
        for segment in segments:
            source.seek(segment.file_offset)
            start = segment.virtual_address
            data = source.read(segment.physical_size)
            if len(data) != segment.physical_size:
                raise ValueError(f"truncated PT_LOAD segment: {path}")
            image[start:start + len(data)] = data
    return elf, image


def init_array_addresses(elf: lief.ELF.Binary, image: bytearray) -> list[int]:
    entries = {item.tag: item.value for item in elf.dynamic_entries}
    address = entries.get(lief.ELF.DYNAMIC_TAGS.INIT_ARRAY, 0)
    size = entries.get(lief.ELF.DYNAMIC_TAGS.INIT_ARRAYSZ, 0)
    return [struct.unpack_from("<Q", image, address + i)[0] for i in range(0, size, 8)]


def load_relative_image(path: Path, libcxx_path: Path | None = None) -> tuple[Uc, dict, list[int], dict[int, str], list[int], list[dict]]:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["libg_sha256"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("libg.so differs from the pinned probe offsets")
    elf, image = read_elf_image(path)
    libraries = [("libg.so", elf, image, BASE)]
    provider_exports: dict[str, int] = {}
    if libcxx_path is not None:
        cpp_elf, cpp_image = read_elf_image(libcxx_path)
        libraries.append(("libc++_shared.so", cpp_elf, cpp_image, CPP_BASE))
        for symbol in cpp_elf.dynamic_symbols:
            if (symbol.name and symbol.shndx != 0
                    and symbol.binding in (lief.ELF.SYMBOL_BINDINGS.GLOBAL,
                                           lief.ELF.SYMBOL_BINDINGS.WEAK)
                    and symbol.visibility in (lief.ELF.SYMBOL_VISIBILITY.DEFAULT,
                                              lief.ELF.SYMBOL_VISIBILITY.PROTECTED)):
                provider_exports.setdefault(symbol.name, CPP_BASE + symbol.value)

    loaded_objects: list[dict] = []
    for name, library_elf, library_image, library_base in libraries:
        header = library_elf.header
        phdr_segment = next((segment for segment in library_elf.segments
                             if segment.type == lief.ELF.SEGMENT_TYPES.PHDR), None)
        if (phdr_segment is None or header.program_header_size != 56
                or header.numberof_segments < 1 or header.numberof_segments > 64
                or phdr_segment.virtual_address + header.numberof_segments * 56 > len(library_image)):
            raise ValueError(f"unsupported guest ELF program header table: {name}")
        executable_load = next((segment for segment in library_elf.segments
                                if segment.type == lief.ELF.SEGMENT_TYPES.LOAD
                                and int(segment.flags) == 5), None)
        if executable_load is None:
            raise ValueError(f"missing read-execute PT_LOAD: {name}")
        loaded_objects.append({
            "name": name,
            "base": library_base,
            "image_size": len(library_image),
            "phdr_address": library_base + phdr_segment.virtual_address,
            "phnum": header.numberof_segments,
            "text_start": library_base + executable_load.virtual_address,
            "text_size": executable_load.virtual_size,
        })

    stub_for_symbol: dict[str, int] = {}
    symbol_for_stub: dict[int, str] = {}
    relocation_stats: dict[str, dict[str, int]] = {}
    for name, library_elf, library_image, library_base in libraries:
        stats = {"relative": 0, "internal_named": 0, "provider": 0,
                 "weak_zero": 0, "trapped_external": 0, "other": 0}
        trapped_symbols: set[str] = set()
        for relocation in library_elf.relocations:
            value: int | None = None
            if relocation.type == R_AARCH64_RELATIVE:
                value = library_base + relocation.addend
                stats["relative"] += 1
            elif (relocation.type in (R_AARCH64_ABS64, R_AARCH64_GLOB_DAT, R_AARCH64_JUMP_SLOT)
                  and relocation.has_symbol):
                symbol = relocation.symbol
                if symbol.shndx != 0:
                    value = library_base + symbol.value + relocation.addend
                    stats["internal_named"] += 1
                elif name == "libg.so" and symbol.name in provider_exports:
                    value = provider_exports[symbol.name] + relocation.addend
                    stats["provider"] += 1
                elif symbol.binding == lief.ELF.SYMBOL_BINDINGS.WEAK:
                    value = relocation.addend
                    stats["weak_zero"] += 1
                else:
                    symbol_name = symbol.name or f"unnamed@{name}+{relocation.address:x}"
                    if symbol_name not in stub_for_symbol:
                        address = STUB + len(stub_for_symbol) * 16
                        stub_for_symbol[symbol_name] = address
                        symbol_for_stub[address] = symbol_name
                    value = stub_for_symbol[symbol_name] + relocation.addend
                    trapped_symbols.add(symbol_name)
                    stats["trapped_external"] += 1
            else:
                stats["other"] += 1
            if value is not None:
                struct.pack_into("<Q", library_image, relocation.address, value)
        stats["trapped_symbols"] = len(trapped_symbols)
        relocation_stats[name] = stats

    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    if not (DATA + PAGE <= STACK and STACK + STACK_SIZE <= RETURN and RETURN < STUB < HEAP):
        raise ValueError("diagnostic memory ranges overlap")
    for _name, _elf, library_image, library_base in libraries:
        machine.mem_map(library_base, len(library_image))
        machine.mem_write(library_base, bytes(library_image))
    machine.mem_map(DATA, PAGE)
    machine.mem_map(STACK, STACK_SIZE)
    machine.reg_write(UC_ARM64_REG_SP, STACK + STACK_SIZE)
    stub_size = round_up(max(PAGE, len(stub_for_symbol) * 16))
    machine.mem_map(STUB, stub_size)
    stub_image = bytearray(stub_size)
    for address in symbol_for_stub:
        struct.pack_into("<I", stub_image, address - STUB, 0xD65F03C0)  # ret
    machine.mem_write(STUB, bytes(stub_image))
    constructors = init_array_addresses(elf, image)
    libg_stats = relocation_stats["libg.so"]
    result = {
        "libg_sha256": actual,
        "mapped_image_bytes": len(image),
        "relative_relocations_applied": libg_stats["relative"],
        "internal_absolute_relocations_applied": libg_stats["internal_named"],
        "external_relocations_resolved_to_local_library": libg_stats["provider"],
        "external_weak_relocations_zeroed": libg_stats["weak_zero"],
        "external_relocations_trapped_not_implemented": libg_stats["trapped_external"],
        "external_symbols_trapped_not_implemented": libg_stats["trapped_symbols"],
        "other_relocations_unresolved": libg_stats["other"],
        "constructor_slots_relocated": sum(BASE <= value < BASE + len(image) for value in constructors),
        "constructor_slots_total": len(constructors),
        "diagnostic_constructors_returned": 0,
        "diagnostic_stack_mapping": {
            "original_bytes": PAGE * 4,
            "mapped_base": hex(STACK),
            "mapped_top": hex(STACK + STACK_SIZE),
            "mapped_bytes": STACK_SIZE,
        },
    }
    cpp_constructors: list[int] = []
    if libcxx_path is not None:
        cpp_stats = relocation_stats["libc++_shared.so"]
        cpp_constructors = init_array_addresses(cpp_elf, cpp_image)
        mutex_slot = struct.unpack_from("<Q", image, 0x18E94E0)[0]
        expected_mutex = provider_exports.get("_ZNSt6__ndk15mutex4lockEv")
        if mutex_slot != expected_mutex:
            raise RuntimeError("libg C++ mutex relocation did not bind to the real provider")
        result["local_library"] = {
            "path": str(libcxx_path),
            "sha256": hashlib.sha256(libcxx_path.read_bytes()).hexdigest(),
            "mapped_base": hex(CPP_BASE),
            "mapped_image_bytes": len(cpp_image),
            "defined_exports": len(provider_exports),
            "relative_relocations_applied": cpp_stats["relative"],
            "internal_named_relocations_applied": cpp_stats["internal_named"],
            "external_weak_relocations_zeroed": cpp_stats["weak_zero"],
            "external_relocations_trapped_not_implemented": cpp_stats["trapped_external"],
            "external_symbols_trapped_not_implemented": cpp_stats["trapped_symbols"],
            "other_relocations_unresolved": cpp_stats["other"],
            "constructor_slots_total": len(cpp_constructors),
            "constructor_slots_relocated": sum(CPP_BASE <= value < CPP_BASE + len(cpp_image)
                                               for value in cpp_constructors),
            "diagnostic_constructors_returned": 0,
            "diagnostic_limit": "constructor returns under selected ABI stubs do not prove full native initialization",
            "libg_mutex_import_points_to_real_cpp_function": mutex_slot == expected_mutex,
            "libg_mutex_import_address": hex(mutex_slot),
        }
    return machine, result, constructors, symbol_for_stub, cpp_constructors, loaded_objects


def call(machine: Uc, offset: int, arg0: int = 0, instruction_budget: int = 1000,
         timeout_microseconds: int = 5_000_000) -> int:
    machine.reg_write(UC_ARM64_REG_X0, arg0)
    machine.reg_write(UC_ARM64_REG_X30, RETURN)
    machine.reg_write(UC_ARM64_REG_SP, STACK + STACK_SIZE)
    machine.emu_start(BASE + offset, RETURN, timeout=timeout_microseconds, count=instruction_budget)
    if machine.reg_read(UC_ARM64_REG_PC) != RETURN:
        raise RuntimeError("guest function did not return within the diagnostic budget")
    return machine.reg_read(UC_ARM64_REG_X0)


def diagnose_constructor_prefix(machine: Uc, constructors: list[int], symbol_for_stub: dict[int, str],
                                real_cpp_mutex_address: int | None = None,
                                dependency_constructors: list[int] | None = None,
                                libg_limit: int = 16,
                                loaded_objects: list[dict] | None = None,
                                diagnose_jni_onload: bool = False,
                                diagnose_application_create: bool = False,
                                diagnose_create_game_main: bool = False,
                                assets_dir_metadata: dict | None = None,
                                angle_dll_dir: Path | None = None
                                ) -> tuple[dict | None, dict | None, dict | None]:
    # These two calls have documented absent-value behavior useful for an
    # isolated diagnostic. Other imports stop execution so no fake
    # initialization is mistaken for a successful native startup.
    faults: list[dict] = []
    imports_seen: list[dict] = []
    unhandled: list[dict] = []
    mutex_adapter_calls: list[dict] = []
    syscall_adapter_calls: list[dict] = []
    condition_adapter_calls: list[dict] = []
    locale_adapter_calls: list[dict] = []
    telemetry_adapter_calls: list[dict] = []
    virtual_sentry_values: dict[int, dict] = {}
    virtual_stdio_calls: list[dict] = []
    asset_manager_bridge_calls: list[dict] = []
    virtual_assets: dict[int, dict] = {}
    next_virtual_asset = VIRTUAL_ASSET_FIRST
    asset_compression_evidence: dict[str, dict] = {}
    graphics_checkpoints: list[dict] = []
    issued_jni_global_refs: dict[int, int] = {}
    virtual_asset_manager_issued = False
    virtual_native_window = {"refcount": 0}
    virtual_egl = {"display_issued": False, "initialized": False,
                   "configs_queried": 0, "choose_queries": 0,
                   "context_issued": False, "surface_issued": False,
                   "current_context_by_thread": {}}
    virtual_gl = {"vertex_arrays": set(), "bound_vertex_array_by_thread": {}}
    angle_backend = None
    if angle_dll_dir is not None:
        from windows_angle_backend import WindowsAngleBackend
        angle_backend = WindowsAngleBackend(angle_dll_dir)
    angle_state = {"display": 0, "config": 0, "context": 0, "surface": 0,
                   "initialized": False, "strings": {},
                   "next_string": GL_VERSION_STRING + PAGE,
                   "vertex_arrays": set(), "textures": set(),
                   "bound_texture_target": None, "bound_texture_name": None,
                   "framebuffers": set(), "bound_framebuffer": None,
                   "samplers": set(),
                   "max_uniform_buffer_bindings": None}
    host_math_functions: dict[str, object] = {}
    resolved_gl_symbols: dict[str, int] = {}
    pinned_package = json.loads(MANIFEST.read_text(encoding="utf-8"))["package"]
    pinned_apk_version = json.loads(MANIFEST.read_text(encoding="utf-8"))["apk_version"]
    if (not isinstance(pinned_package, str) or not pinned_package.isascii()
            or not 0 < len(pinned_package) <= 64 or "\0" in pinned_package):
        raise ValueError("pinned manifest package cannot form a bounded guest argv")
    virtual_cmdline = pinned_package.encode("ascii") + b"\0"
    virtual_file = {"state": "unopened", "cursor": 0}
    virtual_which_su_pipe = {"state": "unopened", "command": None}
    virtual_update_output = {"state": "unopened", "stream": None,
                             "path": None, "bytes_written": 0,
                             "digest": hashlib.sha256()}
    virtual_cache_dir = {"state": "unopened"}
    guest_fs_root = (ROOT / ".local-runtime/windows-guest-fs").resolve()
    if diagnose_create_game_main and assets_dir_metadata is not None:
        if not guest_fs_root.is_relative_to(ROOT.resolve()):
            raise ValueError("guest data root resolves outside the workspace")
        guest_fs_root.mkdir(parents=True, exist_ok=True)
        cache_host_dir = (guest_fs_root / "data" / "user" / "0" / pinned_package
                          / "cache").resolve()
        if not cache_host_dir.is_relative_to(guest_fs_root):
            raise ValueError("guest cache directory resolves outside the workspace")
        cache_host_dir.mkdir(parents=True, exist_ok=True)
    mutexes: dict[int, GuestMutex] = {}
    initialized_mutex_attrs: set[int] = set()
    initialized_cond_attrs: set[int] = set()
    condition_waiters: dict[int, int] = {}
    condition_wait_queue: dict[int, list[int]] = {}
    locale_handles: set[int] = set()
    guest_thread_locale: int | None = None
    thread_keys: dict[int, int] = {}
    thread_key_values: dict[int, dict[int, int]] = {}
    next_thread_key_index = 0
    guest_threads: dict[int, dict] = {}
    once_pending: dict[str, int] = {}
    completed_once_controls: set[int] = set()
    active_guest_thread_id = GUEST_THREAD_ID
    virtual_clock = {"monotonic_ns": VIRTUAL_MONOTONIC_NS,
                     "wall_ns": VIRTUAL_WALL_SECONDS * 1_000_000_000}
    real_cpp_mutex_entries = 0
    machine.mem_map(HEAP, HEAP_SIZE)
    machine.mem_map(GL_STUB_BASE, GL_STUB_SIZE)
    machine.mem_map(GL_VERSION_STRING, 0x10000)
    machine.mem_write(GL_VERSION_STRING, b"OpenGL ES 3.0 Diagnostic\0")
    machine.mem_write(GL_EXTENSION_STRING, b"GL_DIAGNOSTIC_NO_EXTENSIONS\0")
    machine.mem_write(GL_RENDERER_STRING, b"Diagnostic GLES Renderer\0")
    machine.mem_write(GL_VENDOR_STRING, b"Diagnostic\0")
    machine.mem_write(GL_SHADING_LANGUAGE_STRING,
                      b"OpenGL ES GLSL ES 3.00 Diagnostic\0")
    machine.mem_map(DL_ITERATE_PHDR, PAGE)
    machine.mem_write(DL_ITERATE_PHDR, struct.pack("<I", 0xD65F03C0))
    machine.mem_write(DL_ITERATE_CONTINUE, struct.pack("<I", 0xD65F03C0))
    machine.mem_write(PTHREAD_ONCE_RETURN, struct.pack("<I", 0xD65F03C0))
    machine.mem_write(BSEARCH_CONTINUE, struct.pack("<I", 0xD65F03C0))
    next_heap = HEAP
    allocations: dict[int, int] = {}
    phdr_iteration_calls: list[dict] = []
    mprotect_calls: list[dict] = []
    patched_instructions: list[dict] = []
    active_iteration: dict | None = None
    active_bsearch: dict[int, dict] = {}
    phdr_infos: list[dict] = []
    for index, loaded in enumerate(sorted(loaded_objects or [],
                                          key=lambda item: item["name"] != "libc++_shared.so")):
        if index >= 2:
            raise ValueError("diagnostic dl_iterate_phdr supports only the two mapped guest ELF objects")
        info_address = DATA + 0x800 + index * 0x80
        name_address = DATA + 0xA00 + index * 0x80
        name_bytes = b"/diagnostic/" + loaded["name"].encode("ascii") + b"\0"
        if len(name_bytes) > 0x80:
            raise ValueError("guest ELF name exceeds diagnostic metadata slot")
        machine.mem_write(name_address, name_bytes)
        machine.mem_write(info_address, struct.pack(
            "<QQQH6xQQQQ", loaded["base"], name_address,
            loaded["phdr_address"], loaded["phnum"], 0, 0, 0, 0))
        phdr_infos.append({**loaded, "info_address": info_address,
                           "name_address": name_address,
                           "guest_name": name_bytes[:-1].decode("ascii")})
    libg_object = next((item for item in phdr_infos if item["name"] == "libg.so"), None)
    if libg_object is None:
        raise ValueError("missing libg.so guest program header metadata")
    diagnostic_stubs = [
        "getauxval(AT_HWCAP) -> 0", "__system_property_get -> absent",
        "__cxa_atexit -> success without registration", "strlen/strcmp/memcpy/memmove/memset/memcmp",
        "memchr -> bounded guest memory search",
        "malloc/calloc/realloc/free -> guest diagnostic heap",
        "posix_memalign -> bounded aligned guest diagnostic heap allocation",
        "__strlen_chk -> bounded guest C string length with Bionic FORTIFY limit",
        "strnlen -> bounded guest C string length",
        "fopen/fgets/fclose -> one pinned-package virtual /proc/self/cmdline read",
        "stat(known su/Magisk paths and missing update shaders) -> absent in controlled guest filesystem",
        "popen/fgets/pclose -> empty /system/bin/which su output in controlled guest system image",
        "AAssetManager_fromJava and AAsset file APIs -> bounded reads from validated --assets-dir",
        "ANativeWindow_fromSurface -> tracked opaque Windows-owned guest window handle",
        "sentry_clear_modulecache at pinned callsite -> recorded telemetry suppression",
        "sentry_set_tag at pinned callsite -> bounded recorded telemetry suppression",
        "sentry_value_new_breadcrumb at pinned callsite -> tracked telemetry-only value",
        "pthread_mutex_{lock,unlock,init,destroy} -> single-thread Lock/RLock with mirrored Bionic state",
        "pthread_mutexattr_init -> aligned writable guest 8-byte default value 0",
        "pthread_mutexattr_settype(attr, 1) -> tracked recursive attr value 1",
        "pthread_mutexattr_destroy -> tracked guest attr value -1",
        "syscall(__NR_gettid=178) -> current tracked guest thread ID",
        "pthread_self -> current tracked guest thread handle",
        "pthread_create at pinned nOnCreate callsite -> queued guest worker with distinct handle",
        "pthread_getschedparam for queued pool worker -> virtual SCHED_OTHER, priority 0",
        "pthread_setschedparam(SCHED_BATCH) -> EPERM for unprivileged guest app",
        "pthread_cond_{signal,broadcast} -> wake tracked condition waiters",
        "pthread_condattr_init -> tracked 8-byte private realtime default",
        "pthread_condattr_setclock(attr, CLOCK_MONOTONIC) -> guest flag 2",
        "pthread_cond_init(tracked monotonic attr) -> guest state 2 with no waiters",
        "pthread_cond_destroy -> Bionic destroyed state for an unused tracked condition",
        "pthread_cond_{wait,timedwait} -> park tracked worker, release and later reacquire its mutex",
        "pthread_condattr_destroy -> tracked guest attr value 0xdeada11d",
        'newlocale(mask=0x1fbf or 0x6, "C", null) -> opaque 8-byte guest locale, mb_cur_max=1',
        "uselocale -> single-thread tracked C locale or LC_GLOBAL_LOCALE only",
        "mbtowc(null, null, n) -> 0 only under tracked stateless C locale",
        "__ctype_get_mb_cur_max -> read 1 from tracked current C locale object",
        "clock_gettime(CLOCK_MONOTONIC or observed CLOCK_REALTIME) -> virtual time",
        "time(null) -> diagnostic virtual wall time",
        "pthread_key_create -> tracked key with null or observed libc++ destructor",
        "pthread_{get,set}specific -> values scoped to tracked guest thread IDs",
        "pthread_once -> observed libc++ callback in the current guest thread",
        "sysconf -> virtual processor, 4 KiB page, or 64-byte L1 data cache line",
        'dlsym(null, "dl_iterate_phdr") -> loaded guest ELF program headers',
    ]

    def allocate(size: int, alignment: int = 16) -> int:
        nonlocal next_heap
        size = max(1, size)
        aligned = (size + 15) & ~15
        address = (next_heap + alignment - 1) & ~(alignment - 1)
        if address + aligned > HEAP + HEAP_SIZE:
            return 0
        next_heap = address + aligned
        allocations[address] = size
        return address

    def mutex_state_bits(state: GuestMutex) -> int:
        kind = 0x4000 if state.recursive else 0
        locked = 1 if state.depth else 0
        extra_depth = ((state.depth - 1) << 2) if state.recursive and state.depth else 0
        return kind | locked | extra_depth

    def mirror_mutex_state(guest: Uc, pointer: int, state: GuestMutex) -> None:
        guest.mem_write(pointer, struct.pack("<H", mutex_state_bits(state)))
        if state.recursive:
            guest.mem_write(pointer + 4, struct.pack("<I", state.owner_tid or 0))

    def on_angle_import(guest: Uc, symbol: str, caller: int, args: tuple[int, int, int, int],
                        import_call: dict) -> bool:
        if angle_backend is None:
            return False
        arg0, arg1, arg2, arg3 = args

        def stop(reason: str, **details: object) -> bool:
            unhandled.append({**import_call, **details, "reason": reason})
            guest.emu_stop()
            return True

        def finish(result: int | None = None) -> bool:
            if result is not None:
                guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return True

        def read_c_string(pointer: int, limit: int = 128) -> str:
            data = bytearray()
            for index in range(limit):
                byte = guest.mem_read(pointer + index, 1)[0]
                if byte == 0:
                    return data.decode("ascii")
                data.append(byte)
            raise ValueError("guest string exceeds ANGLE bridge bound")

        def guest_gl_string(key: tuple[int, ...], data: bytes | None) -> int:
            if not data:
                return 0
            cached = angle_state["strings"].get(key)
            if cached is not None:
                return cached
            if len(data) > 1024 or b"\0" in data:
                raise ValueError("ANGLE GL string is not a bounded C string")
            pointer = (angle_state["next_string"] + 15) & ~15
            if pointer + len(data) + 1 > GL_VERSION_STRING + 0x10000:
                raise ValueError("ANGLE GL string arena exhausted")
            guest.mem_write(pointer, data + b"\0")
            angle_state["strings"][key] = pointer
            angle_state["next_string"] = pointer + len(data) + 1
            return pointer

        display = angle_state["display"]
        config = angle_state["config"]
        context = angle_state["context"]
        surface = angle_state["surface"]
        if symbol == "eglGetDisplay":
            if caller != BASE + 0x013985B4 or arg0 != 0 or display:
                return stop("unrecognized ANGLE default display request")
            display = angle_backend.get_display(0)
            if not display:
                return stop("native eglGetDisplay failed", egl_error=hex(angle_backend.get_error()))
            angle_state["display"] = display
            virtual_egl["display_issued"] = True
            import_call.update({"native_display": hex(display), "guest_display": hex(VIRTUAL_EGL_DISPLAY)})
            return finish(VIRTUAL_EGL_DISPLAY)
        if symbol == "eglInitialize":
            if caller != BASE + 0x013985CC or arg0 != VIRTUAL_EGL_DISPLAY or not display:
                return stop("unrecognized ANGLE initialize request")
            ok, major, minor = angle_backend.initialize(display)
            if not ok:
                return stop("native eglInitialize failed", egl_error=hex(angle_backend.get_error()))
            guest.mem_write(arg1, struct.pack("<I", major))
            guest.mem_write(arg2, struct.pack("<I", minor))
            angle_state["initialized"] = True
            virtual_egl["initialized"] = True
            import_call.update({"native_version": [major, minor], "result": 1})
            return finish(1)
        if symbol == "eglGetConfigs":
            if arg0 != VIRTUAL_EGL_DISPLAY or not angle_state["initialized"]:
                return stop("ANGLE EGL config enumeration before initialization")
            first = caller == BASE + 0x01398200 and arg1 == 0 and arg2 == 0
            second = caller == BASE + 0x0139823C and arg1 != 0 and arg2 == 1
            if not (first or second):
                return stop("unrecognized ANGLE EGL config enumeration")
            if not config:
                requirements = (0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8,
                                0x3025, 24, 0x3026, 8, 0x3040, 0x40,
                                0x3033, 1, 0x3038)
                ok, configs, count = angle_backend.choose_config(display, requirements)
                if not ok or count < 1 or not configs or not configs[0]:
                    return stop("native ANGLE has no GLES3 pbuffer config",
                                egl_error=hex(angle_backend.get_error()))
                config = configs[0]
                angle_state["config"] = config
            count_out = guest.reg_read(UC_ARM64_REG_X3)
            guest.mem_write(count_out, struct.pack("<I", 1))
            if second:
                guest.mem_write(arg1, struct.pack("<Q", VIRTUAL_EGL_CONFIG))
            virtual_egl["configs_queried"] += 1
            import_call.update({"native_config": hex(config), "guest_config": hex(VIRTUAL_EGL_CONFIG),
                                "config_count_exposed": 1, "result": 1})
            return finish(1)
        if symbol == "eglGetConfigAttrib":
            if arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_CONFIG or not config:
                return stop("unrecognized ANGLE config attribute request", attribute=hex(arg2))
            ok, value = angle_backend.get_config_attrib(display, config, arg2)
            if not ok:
                return stop("native eglGetConfigAttrib failed", attribute=hex(arg2),
                            egl_error=hex(angle_backend.get_error()))
            guest.mem_write(arg3, struct.pack("<i", value))
            import_call.update({"attribute": hex(arg2), "value": value, "result": 1})
            return finish(1)
        if symbol == "eglChooseConfig":
            if arg0 != VIRTUAL_EGL_DISPLAY or not config:
                return stop("unrecognized ANGLE choose-config request")
            attributes = []
            for index in range(32):
                attribute = struct.unpack("<I", guest.mem_read(arg1 + 8 * index, 4))[0]
                if attribute == 0x3038:
                    break
                required = struct.unpack("<I", guest.mem_read(arg1 + 8 * index + 4, 4))[0]
                attributes.append((attribute, required))
            else:
                return stop("ANGLE guest config attribute list too long")
            if [attribute for attribute, _ in attributes] != [
                    0x3040, 0x3024, 0x3023, 0x3022, 0x3021,
                    0x3025, 0x3026, 0x3031]:
                return stop("unrecognized ANGLE guest config requirements",
                            attributes=attributes)
            for attribute, required in attributes:
                ok, available = angle_backend.get_config_attrib(display, config, attribute)
                if not ok or ((available & required) != required if attribute == 0x3040
                              else available < required):
                    return stop("native ANGLE config does not satisfy guest requirements",
                                attribute=hex(attribute), required=required,
                                available=available if ok else None)
            first = caller == BASE + 0x01398378 and arg2 == 0 and arg3 == 0
            second = caller == BASE + 0x013983D0 and arg2 != 0 and arg3 == 1
            if not (first or second):
                return stop("unrecognized ANGLE config selection pass")
            count_out = guest.reg_read(UC_ARM64_REG_X4)
            guest.mem_write(count_out, struct.pack("<I", 1))
            if second:
                guest.mem_write(arg2, struct.pack("<Q", VIRTUAL_EGL_CONFIG))
            virtual_egl["choose_queries"] += 1
            import_call.update({"requirements": [[hex(a), v] for a, v in attributes],
                                "native_config": hex(config), "result": 1})
            return finish(1)
        if symbol == "eglCreateContext":
            attrs = struct.unpack("<IIIII", guest.mem_read(arg3, 20))
            if (caller != BASE + 0x0139867C or arg0 != VIRTUAL_EGL_DISPLAY
                    or arg1 != VIRTUAL_EGL_CONFIG or arg2 != 0
                    or attrs != (0x3098, 3, 0x30FB, 0, 0x3038) or context):
                return stop("unrecognized ANGLE GLES3 context request", attributes=attrs)
            context = angle_backend.create_context(display, config, 3)
            if not context:
                return stop("native eglCreateContext failed", egl_error=hex(angle_backend.get_error()))
            angle_state["context"] = context
            virtual_egl["context_issued"] = True
            import_call.update({"native_context": hex(context), "guest_context": hex(VIRTUAL_EGL_CONTEXT)})
            return finish(VIRTUAL_EGL_CONTEXT)
        if symbol == "eglCreateWindowSurface":
            if (caller != BASE + 0x013DAA78 or arg0 != VIRTUAL_EGL_DISPLAY
                    or arg1 != VIRTUAL_EGL_CONFIG or arg2 != VIRTUAL_NATIVE_WINDOW
                    or arg3 != 0 or not context or surface
                    or virtual_native_window["refcount"] != 1):
                return stop("unrecognized ANGLE Surface-backed pbuffer request")
            surface = angle_backend.create_pbuffer_surface(display, config, 1080, 1920)
            if not surface:
                return stop("native eglCreatePbufferSurface failed",
                            egl_error=hex(angle_backend.get_error()))
            angle_state["surface"] = surface
            virtual_egl["surface_issued"] = True
            import_call.update({"native_pbuffer": hex(surface),
                                "guest_surface": hex(VIRTUAL_EGL_SURFACE),
                                "dimensions": [1080, 1920],
                                "basis": "headless pbuffer for observed SurfaceChanged dimensions"})
            return finish(VIRTUAL_EGL_SURFACE)
        if symbol == "eglGetCurrentContext":
            if caller != BASE + 0x01398108 or not surface:
                return stop("unrecognized ANGLE current-context query")
            current = angle_backend.get_current_context()
            guest_current = VIRTUAL_EGL_CONTEXT if current == context else 0
            import_call.update({"native_current_context": hex(current),
                                "guest_current_context": hex(guest_current)})
            return finish(guest_current)
        if symbol == "eglMakeCurrent":
            first = caller == BASE + 0x01398124 and arg3 == 0
            bind = caller == BASE + 0x013980C8 and arg3 == VIRTUAL_EGL_CONTEXT
            if (arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_SURFACE
                    or arg2 != VIRTUAL_EGL_SURFACE or not surface
                    or not (first or bind)):
                return stop("unrecognized ANGLE make-current request")
            ok = angle_backend.make_current(display, surface, 0 if first else context)
            if bind and not ok:
                return stop("native eglMakeCurrent failed", egl_error=hex(angle_backend.get_error()))
            if bind:
                virtual_egl["current_context_by_thread"][active_guest_thread_id] = VIRTUAL_EGL_CONTEXT
            import_call.update({"native_context": hex(context if bind else 0),
                                "result": int(ok), "guest_thread_id": active_guest_thread_id})
            return finish(int(ok))
        if symbol == "eglGetProcAddress":
            name = read_c_string(arg0)
            if (not BASE + 0x01618500 <= caller < BASE + 0x0161A300
                    or not name.startswith("gl")):
                return stop("unrecognized ANGLE GL resolver request", name=name)
            native_proc = angle_backend.get_proc_address(name)
            if not native_proc:
                import_call.update({"name": name, "guest_function": None,
                                    "native_function": None})
                return finish(0)
            gl_address = resolved_gl_symbols.get(name)
            if gl_address is None:
                gl_address = GL_STUB_BASE + len(resolved_gl_symbols) * 16
                if gl_address + 16 > GL_STUB_BASE + GL_STUB_SIZE:
                    return stop("ANGLE guest GL stub table exhausted")
                resolved_gl_symbols[name] = gl_address
                symbol_for_stub[gl_address] = name
                guest.mem_write(gl_address, struct.pack("<I", 0xD65F03C0))
            import_call.update({"name": name, "guest_function": hex(gl_address),
                                "native_function": hex(native_proc),
                                "basis": "x64 address retained by host; ARM64 sees a trapped guest stub"})
            return finish(gl_address)
        if symbol.startswith("gl"):
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            if current != VIRTUAL_EGL_CONTEXT or angle_backend.get_current_context() != context:
                return stop("ANGLE GL call without the guest thread's bound context",
                            guest_thread_id=active_guest_thread_id)
            if symbol == "glGetString":
                data = angle_backend.get_gl_string(arg0)
                pointer = guest_gl_string((arg0,), data)
                import_call.update({"name": hex(arg0), "value": data.decode("ascii", "replace") if data else None,
                                    "guest_pointer": hex(pointer)})
                return finish(pointer)
            if symbol == "glGetStringi":
                data = angle_backend.get_gl_string_i(arg0, arg1)
                pointer = guest_gl_string((arg0, arg1), data)
                import_call.update({"name": hex(arg0), "index": arg1,
                                    "value": data.decode("ascii", "replace") if data else None,
                                    "guest_pointer": hex(pointer)})
                return finish(pointer)
            if symbol == "glGetIntegerv":
                known_queries = {0x821D, 0x821B, 0x821C, 0x87FE, 0x8A2F,
                                 0x8A30, 0x8A34, 0x0D33}
                if arg0 not in known_queries:
                    return stop("unimplemented ANGLE GL integer query", parameter=hex(arg0))
                value = angle_backend.get_gl_integers(arg0)[0]
                guest.mem_write(arg1, struct.pack("<i", value))
                if arg0 == 0x8A2F:
                    angle_state["max_uniform_buffer_bindings"] = value
                import_call.update({"parameter": hex(arg0), "value": value,
                                    "output": hex(arg1), "source": "native ANGLE"})
                return finish()
            if symbol == "glGetError":
                value = angle_backend.get_gl_error()
                import_call.update({"result": hex(value), "source": "native ANGLE"})
                return finish(value)
            if symbol == "glGetShaderPrecisionFormat":
                if arg0 not in (0x8B31, 0x8B30) or not 0x8DF0 <= arg1 <= 0x8DF5:
                    return stop("unrecognized ANGLE shader precision query")
                lower, upper, bits = angle_backend.get_shader_precision_format(arg0, arg1)
                guest.mem_write(arg2, struct.pack("<ii", lower, upper))
                guest.mem_write(arg3, struct.pack("<i", bits))
                import_call.update({"shader": hex(arg0), "precision_type": hex(arg1),
                                    "range": [lower, upper], "precision": bits,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glGenVertexArrays":
                if caller != BASE + 0x013DB1DC or arg0 != 1:
                    return stop("unrecognized ANGLE VAO creation")
                names = angle_backend.gen_vertex_arrays(1)
                if len(names) != 1 or names[0] == 0:
                    return stop("native ANGLE glGenVertexArrays failed",
                                gl_error=hex(angle_backend.get_gl_error()))
                angle_state["vertex_arrays"].add(names[0])
                guest.mem_write(arg1, struct.pack("<I", names[0]))
                import_call.update({"name": names[0], "output": hex(arg1), "source": "native ANGLE"})
                return finish()
            if symbol == "glBindVertexArray":
                if caller != BASE + 0x013DB1F0 or arg0 not in angle_state["vertex_arrays"]:
                    return stop("unrecognized ANGLE VAO binding", name=arg0)
                angle_backend.bind_vertex_array(arg0)
                import_call.update({"name": arg0, "source": "native ANGLE"})
                return finish()
            if symbol == "glGenTextures":
                if caller != BASE + 0x013F1004 or arg0 != 1:
                    return stop("unrecognized ANGLE texture creation")
                names = angle_backend.gen_textures(1)
                if len(names) != 1 or names[0] == 0:
                    return stop("native ANGLE glGenTextures failed",
                                gl_error=hex(angle_backend.get_gl_error()))
                angle_state["textures"].add(names[0])
                guest.mem_write(arg1, struct.pack("<I", names[0]))
                import_call.update({"name": names[0], "output": hex(arg1), "source": "native ANGLE"})
                return finish()
            if symbol == "glActiveTexture":
                if (caller not in (BASE + 0x013F1024, BASE + 0x013F1878)
                        or arg0 != 0x84C0):
                    return stop("unrecognized ANGLE texture unit", unit=hex(arg0))
                angle_backend.active_texture(arg0)
                import_call.update({"unit": hex(arg0), "source": "native ANGLE"})
                return finish()
            if symbol == "glBindTexture":
                if (caller not in (BASE + 0x013F103C, BASE + 0x013F1890,
                                   BASE + 0x013F1C74, BASE + 0x013F1D3C)
                        or arg0 not in
                        (0x0DE1, 0x806F, 0x8513, 0x8C1A)
                        or (arg1 not in angle_state["textures"] and
                            not (caller in (BASE + 0x013F1890,
                                            BASE + 0x013F1D3C) and arg1 == 0))):
                    return stop("unrecognized ANGLE texture binding",
                                target=hex(arg0), name=arg1)
                angle_backend.bind_texture(arg0, arg1)
                angle_state["bound_texture_target"] = arg0
                angle_state["bound_texture_name"] = arg1
                import_call.update({"target": hex(arg0), "name": arg1,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glTexParameteri":
                if (caller not in (BASE + offset for offset in
                        (0x013F105C, 0x013F1070, 0x013F1084,
                         0x013F1098, 0x013F10AC, 0x013F10C0,
                         0x013F1C90, 0x013F1CA4, 0x013F1CB8,
                         0x013F1CCC, 0x013F1CE0,
                         0x013F1D10, 0x013F1D24))
                        or arg0 != angle_state["bound_texture_target"]
                        or arg1 not in (0x813D, 0x2802, 0x2803, 0x8072,
                                        0x2801, 0x2800, 0x884C, 0x884D)):
                    return stop("unrecognized ANGLE texture parameter",
                                target=hex(arg0), parameter=hex(arg1), value=arg2)
                angle_backend.tex_parameter_i(arg0, arg1, arg2)
                import_call.update({"target": hex(arg0), "parameter": hex(arg1),
                                    "value": arg2, "source": "native ANGLE"})
                return finish()
            if symbol == "glPixelStorei":
                if (caller != BASE + 0x013F1130 or arg0 != 0x0CF5
                        or arg1 not in (1, 2, 4, 8)):
                    return stop("unrecognized ANGLE pixel packing",
                                parameter=hex(arg0), value=arg1)
                angle_backend.pixel_store_i(arg0, arg1)
                import_call.update({"parameter": hex(arg0), "value": arg1,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glTexImage2D":
                extra = [guest.reg_read(register) for register in
                         (UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                          UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
                pixels_pointer = struct.unpack(
                    "<Q", guest.mem_read(guest.reg_read(UC_ARM64_REG_SP), 8))[0]
                target, level, internal_format, width = (
                    arg0, arg1, arg2 & 0xFFFFFFFF, arg3)
                height, border, pixel_format, pixel_type = extra
                expected_2d = (caller == BASE + 0x013F1814
                               and target == 0x0DE1
                               and angle_state["bound_texture_target"] == 0x0DE1)
                cube_face_calls = (0x013F155C, 0x013F159C, 0x013F15DC,
                                   0x013F1620, 0x013F1660, 0x013F16A4)
                expected_cube = (0x8515 <= target <= 0x851A
                                 and caller == BASE + cube_face_calls[target - 0x8515]
                                 and angle_state["bound_texture_target"] == 0x8513)
                if (not (expected_2d or expected_cube)
                        or level > 16 or internal_format != 0x8058
                        or not 0 < width <= 4096 or not 0 < height <= 4096
                        or width * height * 4 > 64 * 1024 * 1024
                        or border != 0 or pixel_format != 0x1908
                        or pixel_type != 0x1401):
                    return stop("unrecognized ANGLE RGBA texture upload",
                                guest_args=import_call.get("guest_args"))
                pixel_count = width * height * 4
                pixels = (bytes(guest.mem_read(pixels_pointer, pixel_count))
                          if pixels_pointer else None)
                angle_backend.tex_image_2d(target, level, internal_format,
                                           width, height, border, pixel_format,
                                           pixel_type, pixels)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE texture upload failed", gl_error=hex(error))
                import_call.update({"target": hex(target), "level": level,
                                    "size": [width, height], "pixel_bytes":
                                    pixel_count if pixels is not None else 0,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glTexStorage2D":
                height = guest.reg_read(UC_ARM64_REG_X4)
                internal_format = arg2 & 0xFFFFFFFF
                if (caller not in (BASE + 0x013F1A48, BASE + 0x013F1A68)
                        or arg0 != angle_state["bound_texture_target"]
                        or not 1 <= arg1 <= 16
                        or not 1 <= arg3 <= 4096 or not 1 <= height <= 4096
                        or arg3 * height > 4096 * 4096):
                    return stop("unrecognized ANGLE immutable texture allocation",
                                target=hex(arg0), levels=arg1,
                                internal_format=hex(internal_format), size=[arg3, height])
                angle_backend.tex_storage_2d(arg0, arg1, internal_format, arg3, height)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE immutable texture allocation failed",
                                gl_error=hex(error), internal_format=hex(internal_format))
                import_call.update({"target": hex(arg0), "levels": arg1,
                                    "internal_format": hex(internal_format),
                                    "size": [arg3, height], "source": "native ANGLE"})
                return finish()
            if symbol == "glGenSamplers":
                if caller != BASE + 0x013F1F24 or arg0 != 1:
                    return stop("unrecognized ANGLE sampler creation", count=arg0)
                names = angle_backend.gen_samplers(1)
                if len(names) != 1 or names[0] == 0:
                    return stop("native ANGLE sampler creation failed",
                                gl_error=hex(angle_backend.get_gl_error()))
                angle_state["samplers"].add(names[0])
                guest.mem_write(arg1, struct.pack("<I", names[0]))
                import_call.update({"name": names[0], "output": hex(arg1),
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glSamplerParameteri":
                parameter_calls = {
                    0x013F1F94: 0x2802, 0x013F1FA8: 0x2803,
                    0x013F1FBC: 0x8072, 0x013F1FD0: 0x2801,
                    0x013F1FE4: 0x2800, 0x013F200C: 0x884C,
                    0x013F2020: 0x884D,
                }
                if (arg0 not in angle_state["samplers"]
                        or parameter_calls.get(caller - BASE) != arg1):
                    return stop("unrecognized ANGLE sampler parameter",
                                sampler=arg0, parameter=hex(arg1), value=arg2)
                angle_backend.sampler_parameter_i(arg0, arg1, arg2)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE sampler parameter failed",
                                gl_error=hex(error), parameter=hex(arg1), value=arg2)
                import_call.update({"sampler": arg0, "parameter": hex(arg1),
                                    "value": arg2, "source": "native ANGLE"})
                return finish()
            if symbol == "glGenFramebuffers":
                if caller != BASE + 0x013E6FF0 or arg0 != 1:
                    return stop("unrecognized ANGLE framebuffer creation")
                names = angle_backend.gen_framebuffers(1)
                if len(names) != 1 or names[0] == 0:
                    return stop("native ANGLE glGenFramebuffers failed",
                                gl_error=hex(angle_backend.get_gl_error()))
                angle_state["framebuffers"].add(names[0])
                guest.mem_write(arg1, struct.pack("<I", names[0]))
                import_call.update({"name": names[0], "output": hex(arg1),
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glDeleteFramebuffers":
                if caller != BASE + 0x013E7724 or arg0 != 1:
                    return stop("unrecognized ANGLE framebuffer deletion", count=arg0)
                name = struct.unpack("<I", guest.mem_read(arg1, 4))[0]
                if name not in angle_state["framebuffers"]:
                    return stop("unrecognized ANGLE framebuffer name", name=name)
                angle_backend.delete_framebuffers([name])
                angle_state["framebuffers"].remove(name)
                if angle_state["bound_framebuffer"] == name:
                    angle_state["bound_framebuffer"] = None
                import_call.update({"name": name, "source": "native ANGLE"})
                return finish()
            if symbol == "glBindFramebuffer":
                if (caller not in (BASE + 0x013E7008, BASE + 0x013E77D8)
                        or arg0 != 0x8D40
                        or arg1 not in angle_state["framebuffers"]):
                    return stop("unrecognized ANGLE framebuffer binding",
                                target=hex(arg0), name=arg1)
                angle_backend.bind_framebuffer(arg0, arg1)
                angle_state["bound_framebuffer"] = arg1
                import_call.update({"target": hex(arg0), "name": arg1,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glFramebufferTexture2D":
                texture_target = arg2 & 0xFFFFFFFF
                level = guest.reg_read(UC_ARM64_REG_X4) & 0xFFFFFFFF
                if (caller != BASE + 0x013E6BEC or arg0 != 0x8D40
                        or angle_state["bound_framebuffer"] is None
                        or arg1 not in (*range(0x8CE0, 0x8CF0), 0x8D00, 0x8D20, 0x821A)
                        or texture_target not in (0x0DE1, *range(0x8515, 0x851B))
                        or arg3 not in angle_state["textures"] or level > 16):
                    return stop("unrecognized ANGLE framebuffer texture attachment",
                                target=hex(arg0), attachment=hex(arg1),
                                texture_target=hex(texture_target), name=arg3,
                                level=level)
                angle_backend.framebuffer_texture_2d(arg0, arg1, texture_target,
                                                     arg3, level)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE framebuffer texture attachment failed",
                                gl_error=hex(error))
                import_call.update({"target": hex(arg0), "attachment": hex(arg1),
                                    "texture_target": hex(texture_target),
                                    "name": arg3, "level": level,
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glDisable":
                disable_caps = {
                    0x013E2B50: 0x0B71, 0x013E2B5C: 0x0B90,
                    0x013E2B68: 0x0C11, 0x013E2B74: 0x0B44,
                    0x013E2B80: 0x0BD0, 0x013E2B8C: 0x8037,
                    0x013E2B98: 0x809E, 0x013E2BA4: 0x80A0,
                }
                if disable_caps.get(caller - BASE) != arg0:
                    return stop("unrecognized ANGLE capability disable",
                                capability=hex(arg0))
                angle_backend.disable(arg0)
                import_call.update({"capability": hex(arg0), "source": "native ANGLE"})
                return finish()
            if symbol == "glDepthMask":
                known_depth_mask = {
                    BASE + 0x013E2BB8: 0,
                    BASE + 0x013E7A74: 1,
                    BASE + 0x013E7A9C: 0,
                }
                if known_depth_mask.get(caller) != arg0:
                    return stop("unrecognized ANGLE depth write mask", value=arg0)
                angle_backend.depth_mask(bool(arg0))
                import_call.update({"enabled": bool(arg0), "source": "native ANGLE"})
                return finish()
            if symbol == "glViewport":
                coordinates = [struct.unpack("<i", struct.pack("<I", value & 0xFFFFFFFF))[0]
                               for value in (arg0, arg1, arg2, arg3)]
                x, y, width, height = coordinates
                if (caller not in (BASE + 0x013E77A0, BASE + 0x013E2ACC)
                        or not -8192 <= x <= 8192 or not -8192 <= y <= 8192
                        or not 0 < width <= 8192 or not 0 < height <= 8192):
                    return stop("unrecognized ANGLE viewport", rectangle=coordinates)
                angle_backend.viewport(x, y, width, height)
                import_call.update({"rectangle": coordinates, "source": "native ANGLE"})
                return finish()
            if symbol == "glDepthRangef":
                near = struct.unpack("<f", struct.pack(
                    "<I", guest.reg_read(UC_ARM64_REG_S0) & 0xFFFFFFFF))[0]
                far = struct.unpack("<f", struct.pack(
                    "<I", guest.reg_read(UC_ARM64_REG_S1) & 0xFFFFFFFF))[0]
                if (caller != BASE + 0x013E4670
                        or not 0.0 <= near <= 1.0 or not 0.0 <= far <= 1.0):
                    return stop("unrecognized ANGLE depth range", values=[near, far])
                angle_backend.depth_range_f(near, far)
                import_call.update({"values": [near, far], "source": "native ANGLE"})
                return finish()
            if symbol == "glScissor":
                coordinates = [struct.unpack("<i", struct.pack("<I", value & 0xFFFFFFFF))[0]
                               for value in (arg0, arg1, arg2, arg3)]
                x, y, width, height = coordinates
                if (caller != BASE + 0x013E77C0
                        or not -8192 <= x <= 8192 or not -8192 <= y <= 8192
                        or not 0 < width <= 8192 or not 0 < height <= 8192):
                    return stop("unrecognized ANGLE scissor", rectangle=coordinates)
                angle_backend.scissor(x, y, width, height)
                import_call.update({"rectangle": coordinates, "source": "native ANGLE"})
                return finish()
            if symbol == "glReadBuffer":
                if (caller != BASE + 0x013E7830 or arg0 != 0
                        or angle_state["bound_framebuffer"] is None):
                    return stop("unrecognized ANGLE read buffer", source=hex(arg0))
                angle_backend.read_buffer(arg0)
                import_call.update({"source_enum": hex(arg0), "source": "native ANGLE"})
                return finish()
            if symbol == "glDrawBuffers":
                if (caller != BASE + 0x013E7848 or arg0 != 4
                        or angle_state["bound_framebuffer"] is None):
                    return stop("unrecognized ANGLE draw buffer selection", count=arg0)
                buffers = list(struct.unpack("<IIII", guest.mem_read(arg1, 16)))
                if any(buffer not in (0, 0x8CE0, 0x8CE1, 0x8CE2, 0x8CE3)
                       for buffer in buffers):
                    return stop("unrecognized ANGLE draw buffer enums", buffers=buffers)
                angle_backend.draw_buffers(buffers)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE draw buffer selection failed",
                                gl_error=hex(error), buffers=buffers)
                import_call.update({"buffers": [hex(value) for value in buffers],
                                    "source": "native ANGLE"})
                return finish()
            if symbol == "glClearBufferfv":
                expected = {
                    BASE + 0x013E78AC: 0x1800,
                    BASE + 0x013E7A90: 0x1801,
                }
                if (expected.get(caller) != arg0 or arg1 != 0
                        or angle_state["bound_framebuffer"] is None):
                    return stop("unrecognized ANGLE color/depth buffer clear",
                                buffer=hex(arg0), draw_buffer=arg1)
                component_count = 4 if arg0 == 0x1800 else 1
                values = list(struct.unpack("<" + "f" * component_count,
                                            guest.mem_read(arg2, 4 * component_count)))
                angle_backend.clear_buffer_fv(arg0, arg1, values)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE color/depth buffer clear failed",
                                gl_error=hex(error), values=values)
                import_call.update({"buffer": hex(arg0), "draw_buffer": arg1,
                                    "values": values, "source": "native ANGLE"})
                return finish()
            if symbol == "glStencilMask":
                expected = {BASE + 0x013E7AC4: 0xFF,
                            BASE + 0x013E7AEC: 0}
                if expected.get(caller) != arg0:
                    return stop("unrecognized ANGLE stencil write mask", mask=hex(arg0))
                angle_backend.stencil_mask(arg0)
                import_call.update({"mask": hex(arg0), "source": "native ANGLE"})
                return finish()
            if symbol == "glClearBufferiv":
                if (caller != BASE + 0x013E7AE0 or arg0 != 0x1802
                        or arg1 != 0 or angle_state["bound_framebuffer"] is None):
                    return stop("unrecognized ANGLE stencil buffer clear",
                                buffer=hex(arg0), draw_buffer=arg1)
                value = struct.unpack("<i", guest.mem_read(arg2, 4))[0]
                angle_backend.clear_buffer_iv(arg0, arg1, [value])
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE stencil buffer clear failed",
                                gl_error=hex(error), value=value)
                import_call.update({"buffer": hex(arg0), "draw_buffer": arg1,
                                    "value": value, "source": "native ANGLE"})
                return finish()
            if symbol == "glBindBufferBase":
                limit = angle_state["max_uniform_buffer_bindings"]
                if (not BASE + 0x013E2F9C <= caller <= BASE + 0x013E3100
                        or arg0 != 0x8A11 or arg2 != 0
                        or limit is None or not 0 <= arg1 < limit):
                    return stop("unrecognized ANGLE uniform buffer reset",
                                target=hex(arg0), index=arg1, name=arg2,
                                native_binding_limit=limit)
                angle_backend.bind_buffer_base(arg0, arg1, arg2)
                error = angle_backend.get_gl_error()
                if error:
                    return stop("native ANGLE uniform buffer reset failed",
                                gl_error=hex(error), index=arg1)
                import_call.update({"target": hex(arg0), "index": arg1,
                                    "name": arg2, "source": "native ANGLE"})
                return finish()
            if symbol == "glFlush":
                if caller != BASE + 0x013E54A4:
                    return stop("unrecognized ANGLE flush request")
                angle_backend.flush()
                import_call.update({"source": "native ANGLE"})
                return finish()
        return False

    def on_import(guest: Uc, address: int, _size: int, _user_data: object) -> None:
        nonlocal guest_thread_locale, next_thread_key_index, virtual_asset_manager_issued
        nonlocal next_virtual_asset
        symbol = symbol_for_stub.get(address, f"unknown-stub@{address:x}")
        caller = guest.reg_read(UC_ARM64_REG_X30) - 4
        import_call = {"symbol": symbol, "caller": hex(caller)}
        imports_seen.append(import_call)
        arg0 = guest.reg_read(UC_ARM64_REG_X0)
        arg1 = guest.reg_read(UC_ARM64_REG_X1)
        arg2 = guest.reg_read(UC_ARM64_REG_X2)
        arg3 = guest.reg_read(UC_ARM64_REG_X3)
        if symbol in ("glTexImage2D", "glTexSubImage2D"):
            extra_registers = [guest.reg_read(register) for register in
                               (UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
            stack_pointer = guest.reg_read(UC_ARM64_REG_SP)
            stack_pointer_value = struct.unpack("<Q", guest.mem_read(stack_pointer, 8))[0]
            import_call["guest_args"] = [hex(value) for value in
                                         (arg0, arg1, arg2, arg3,
                                          *extra_registers, stack_pointer_value)]
        if angle_backend is not None and (symbol.startswith("egl") or symbol.startswith("gl")):
            try:
                handled = on_angle_import(guest, symbol, caller,
                                          (arg0, arg1, arg2, arg3), import_call)
            except (UcError, ValueError, OSError, RuntimeError) as error:
                unhandled.append({**import_call, "reason": str(error),
                                  "bridge": "native ANGLE"})
                guest.emu_stop()
                return
            if handled:
                return
            unhandled.append({**import_call, "reason": "ANGLE guest bridge has no implementation",
                              "bridge": "native ANGLE"})
            guest.emu_stop()
            return
        if symbol == "eglGetProcAddress":
            try:
                name_bytes = bytearray()
                for index in range(128):
                    byte = guest.mem_read(arg0 + index, 1)[0]
                    if byte == 0:
                        break
                    name_bytes.append(byte)
                else:
                    raise ValueError("GL function name exceeds diagnostic bound")
                name = name_bytes.decode("ascii")
            except (UcError, UnicodeDecodeError, ValueError) as error:
                unhandled.append({**import_call, "name_pointer": hex(arg0),
                                  "reason": str(error)})
                guest.emu_stop()
                return
            if (not BASE + 0x01618500 <= caller < BASE + 0x0161A300
                    or not name.startswith("gl")
                    or not name[2:].replace("_", "").isalnum()
                    or len(resolved_gl_symbols) >= GL_STUB_SIZE // 16):
                unhandled.append({**import_call, "name": name,
                                  "reason": "unrecognized GL resolver request"})
                guest.emu_stop()
                return
            gl_address = resolved_gl_symbols.get(name)
            if gl_address is None:
                gl_address = GL_STUB_BASE + len(resolved_gl_symbols) * 16
                resolved_gl_symbols[name] = gl_address
                symbol_for_stub[gl_address] = name
                guest.mem_write(gl_address, struct.pack("<I", 0xD65F03C0))
            import_call.update({"name": name, "guest_function": hex(gl_address),
                                "behavior": "diagnostic trap on actual guest invocation"})
            guest.reg_write(UC_ARM64_REG_X0, gl_address)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGetString":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            string_queries = {
                (BASE + 0x01618578, 0x1F02):
                    ("GL_VERSION", GL_VERSION_STRING, "OpenGL ES 3.0 Diagnostic"),
                (BASE + 0x01618588, 0x1F02):
                    ("GL_VERSION", GL_VERSION_STRING, "OpenGL ES 3.0 Diagnostic"),
                (BASE + 0x013DAC34, 0x1F01):
                    ("GL_RENDERER", GL_RENDERER_STRING, "Diagnostic GLES Renderer"),
                (BASE + 0x013DAC44, 0x1F00):
                    ("GL_VENDOR", GL_VENDOR_STRING, "Diagnostic"),
                (BASE + 0x013DAC54, 0x1F02):
                    ("GL_VERSION", GL_VERSION_STRING, "OpenGL ES 3.0 Diagnostic"),
                (BASE + 0x013DAC64, 0x8B8C):
                    ("GL_SHADING_LANGUAGE_VERSION", GL_SHADING_LANGUAGE_STRING,
                     "OpenGL ES GLSL ES 3.00 Diagnostic"),
            }
            query = string_queries.get((caller, arg0))
            if query is None or current != VIRTUAL_EGL_CONTEXT:
                unhandled.append({**import_call, "name": hex(arg0),
                                  "current_context": hex(current or 0),
                                  "reason": "only observed GL identity queries on bound GLES3 context are supported"})
                guest.emu_stop()
                return
            query_name, result_address, query_value = query
            import_call.update({"name": query_name, "result": hex(result_address),
                                "value": query_value,
                                "basis": "diagnostic branch selection; no GL rendering backend"})
            guest.reg_write(UC_ARM64_REG_X0, result_address)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGetIntegerv":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            query_values = {
                (BASE + 0x0161A284, 0x821D, BASE + 0x01A15ECC):
                    ("GL_NUM_EXTENSIONS", 1),
                (BASE + 0x013DAC10, 0x821B, 0x674FFEAC):
                    ("GL_MAJOR_VERSION", 3),
                (BASE + 0x013DAC20, 0x821C, 0x674FFEA8):
                    ("GL_MINOR_VERSION", 0),
                (BASE + 0x013DAD1C, 0x821D, 0x674FFE5C):
                    ("GL_NUM_EXTENSIONS", 1),
                (BASE + 0x013DAF94, 0x87FE, 0x674FFED0):
                    ("GL_NUM_PROGRAM_BINARY_FORMATS", 0),
                (BASE + 0x013DAFF8, 0x8A2F, 0x674FFE90):
                    ("GL_MAX_UNIFORM_BUFFER_BINDINGS", 24),
                (BASE + 0x013DB014, 0x8A30, 0x674FFE80):
                    ("GL_MAX_UNIFORM_BLOCK_SIZE", 16384),
                (BASE + 0x013DB030, 0x8A34, 0x674FFE70):
                    ("GL_UNIFORM_BUFFER_OFFSET_ALIGNMENT", 256),
                (BASE + 0x013DB05C, 0x0D33, 0x674FFE60):
                    ("GL_MAX_TEXTURE_SIZE", 2048),
            }
            query = query_values.get((caller, arg0, arg1))
            if query is None or current != VIRTUAL_EGL_CONTEXT:
                unhandled.append({**import_call, "parameter": hex(arg0),
                                  "value_out": hex(arg1),
                                  "current_context": hex(current or 0),
                                  "reason": "only observed diagnostic GLES version queries are supported"})
                guest.emu_stop()
                return
            name, value = query
            guest.mem_write(arg1, struct.pack("<I", value))
            import_call.update({"parameter": name, "value_out": hex(arg1),
                                "value": value,
                                "basis": "diagnostic virtual GLES3 context, no real rendering backend"})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGetStringi":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            if (caller not in (BASE + 0x0161A30C, BASE + 0x013DAD7C)
                    or arg0 != 0x1F03 or arg1 != 0
                    or current != VIRTUAL_EGL_CONTEXT):
                unhandled.append({**import_call, "name": hex(arg0), "index": arg1,
                                  "current_context": hex(current or 0),
                                  "reason": "only the synthetic first diagnostic extension is supported"})
                guest.emu_stop()
                return
            import_call.update({"name": "GL_EXTENSIONS", "index": 0,
                                "value": "GL_DIAGNOSTIC_NO_EXTENSIONS",
                                "result": hex(GL_EXTENSION_STRING),
                                "basis": "loader traversal only; no real GLES extension"})
            guest.reg_write(UC_ARM64_REG_X0, GL_EXTENSION_STRING)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGetError":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            if caller != BASE + 0x013DAFA4 or current != VIRTUAL_EGL_CONTEXT:
                unhandled.append({**import_call, "current_context": hex(current or 0),
                                  "reason": "unrecognized GLES error query"})
                guest.emu_stop()
                return
            import_call.update({"result": "GL_NO_ERROR", "basis": "no prior diagnostic GL error"})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGetShaderPrecisionFormat":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            offset = caller - (BASE + 0x013DB088)
            query_index = offset // 0x18 if offset >= 0 and offset % 0x18 == 0 else -1
            precision_values = ((8, 8, 8), (14, 14, 10), (62, 62, 16),
                                (8, 8, 0), (10, 10, 0), (16, 16, 0))
            expected_shader = (0x8B31 if 0 <= query_index < 6 else
                               0x8B30 if 6 <= query_index < 12 else None)
            expected_precision = (0x8DF0 + query_index % 6
                                  if 0 <= query_index < 12 else None)
            if (current != VIRTUAL_EGL_CONTEXT or arg0 != expected_shader
                    or arg1 != expected_precision
                    or arg2 != 0x674FFEB0 or arg3 != 0x674FFE48):
                unhandled.append({**import_call, "shader": hex(arg0),
                                  "precision_type": hex(arg1),
                                  "range_out": hex(arg2), "precision_out": hex(arg3),
                                  "query_index": query_index,
                                  "reason": "unrecognized diagnostic shader precision query"})
                guest.emu_stop()
                return
            minimum, maximum, bits = precision_values[query_index % 6]
            guest.mem_write(arg2, struct.pack("<II", minimum, maximum))
            guest.mem_write(arg3, struct.pack("<I", bits))
            import_call.update({"shader": hex(arg0), "precision_type": hex(arg1),
                                "range": [minimum, maximum], "precision": bits,
                                "query_index": query_index,
                                "basis": "diagnostic GLES3 precision capability"})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glGenVertexArrays":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            if (caller != BASE + 0x013DB1DC or arg0 != 1 or arg1 != 0x70222C78
                    or current != VIRTUAL_EGL_CONTEXT
                    or virtual_gl["vertex_arrays"]):
                unhandled.append({**import_call, "count": arg0, "arrays_out": hex(arg1),
                                  "current_context": hex(current or 0),
                                  "reason": "only the first observed virtual VAO creation is supported"})
                guest.emu_stop()
                return
            virtual_gl["vertex_arrays"].add(1)
            guest.mem_write(arg1, struct.pack("<I", 1))
            import_call.update({"count": 1, "arrays_out": hex(arg1),
                                "generated_names": [1],
                                "basis": "diagnostic VAO identity only"})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "glBindVertexArray":
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id)
            if (caller != BASE + 0x013DB1F0 or arg0 != 1
                    or arg0 not in virtual_gl["vertex_arrays"]
                    or current != VIRTUAL_EGL_CONTEXT):
                unhandled.append({**import_call, "array_name": arg0,
                                  "current_context": hex(current or 0),
                                  "reason": "unrecognized virtual VAO binding"})
                guest.emu_stop()
                return
            virtual_gl["bound_vertex_array_by_thread"][active_guest_thread_id] = arg0
            import_call.update({"array_name": arg0, "guest_thread_id": active_guest_thread_id,
                                "basis": "diagnostic VAO binding only"})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "strncmp":
            if arg2 > 4096:
                unhandled.append({**import_call, "limit": arg2,
                                  "reason": "strncmp exceeds diagnostic bound"})
                guest.emu_stop()
                return
            try:
                compared = []
                comparison = 0
                for index in range(arg2):
                    left = guest.mem_read(arg0 + index, 1)[0]
                    right = guest.mem_read(arg1 + index, 1)[0]
                    compared.append([left, right])
                    if left != right or left == 0 or right == 0:
                        comparison = left - right
                        break
            except UcError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"left": hex(arg0), "right": hex(arg1),
                                "limit": arg2, "bytes_compared": len(compared),
                                "result": comparison})
            guest.reg_write(UC_ARM64_REG_X0, comparison & 0xFFFFFFFFFFFFFFFF)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "strncasecmp":
            if arg2 > 4096:
                unhandled.append({**import_call, "limit": arg2,
                                  "reason": "strncasecmp exceeds diagnostic bound"})
                guest.emu_stop()
                return
            try:
                comparison = 0
                compared = 0
                for index in range(arg2):
                    left = guest.mem_read(arg0 + index, 1)[0]
                    right = guest.mem_read(arg1 + index, 1)[0]
                    if left > 0x7F or right > 0x7F:
                        raise ValueError("strncasecmp input is not ASCII")
                    left = left + 32 if 65 <= left <= 90 else left
                    right = right + 32 if 65 <= right <= 90 else right
                    compared += 1
                    if left != right or left == 0:
                        comparison = left - right
                        break
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"limit": arg2, "bytes_compared": compared,
                                "result": comparison})
            guest.reg_write(UC_ARM64_REG_X0, comparison & 0xFFFFFFFFFFFFFFFF)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sscanf":
            try:
                source = bytes(guest.mem_read(arg0, 128)).split(b"\0", 1)[0]
                format_string = bytes(guest.mem_read(arg1, 16)).split(b"\0", 1)[0]
            except UcError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            output_writable = all(
                pointer != 0 and pointer & 3 == 0 and any(
                    begin <= pointer and pointer + 4 <= end
                    and permissions & UC_PROT_WRITE
                    for begin, end, permissions in guest.mem_regions())
                for pointer in (arg2, arg3))
            expected_version_pointer = (
                angle_state["strings"].get((0x1F02,), 0) + 10
                if angle_backend is not None else GL_VERSION_STRING + 10)
            parsed_version = re.match(rb"([0-9]{1,2})\.([0-9]{1,2})", source)
            if (caller != BASE + 0x01618608
                    or arg0 != expected_version_pointer
                    or parsed_version is None
                    or format_string != b"%d.%d" or not output_writable):
                unhandled.append({**import_call, "source": source.decode("ascii", "replace"),
                                  "format": format_string.decode("ascii", "replace"),
                                  "major_out": hex(arg2), "minor_out": hex(arg3),
                                  "reason": "unrecognized bounded GLES version parse"})
                guest.emu_stop()
                return
            major = int(parsed_version.group(1))
            minor = int(parsed_version.group(2))
            guest.mem_write(arg2, struct.pack("<I", major))
            guest.mem_write(arg3, struct.pack("<I", minor))
            import_call.update({"source": source.decode("ascii"), "format": "%d.%d",
                                "major_out": hex(arg2), "minor_out": hex(arg3),
                                "version": [major, minor], "result": 2})
            guest.reg_write(UC_ARM64_REG_X0, 2)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "strncpy":
            if (caller not in {BASE + offset for offset in (
                    0x013DAE9C, 0x013DAEB8, 0x013DAED8,
                    0x013DAEF8, 0x013DAF18)}
                    or arg2 != 0x100
                    or not any(begin <= arg0 and arg0 + arg2 <= end
                               and permissions & UC_PROT_WRITE
                               for begin, end, permissions in guest.mem_regions())):
                unhandled.append({**import_call, "destination": hex(arg0),
                                  "source": hex(arg1), "limit": arg2,
                                  "reason": "unrecognized bounded GL identity copy"})
                guest.emu_stop()
                return
            try:
                payload = bytearray()
                for index in range(arg2):
                    value = guest.mem_read(arg1 + index, 1)[0]
                    payload.append(value)
                    if value == 0:
                        break
            except UcError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            guest.mem_write(arg0, bytes(payload).ljust(arg2, b"\0"))
            import_call.update({"destination": hex(arg0), "source": hex(arg1),
                                "limit": arg2,
                                "source_text": bytes(payload).split(b"\0", 1)[0].decode(
                                    "utf-8", "replace")})
            guest.reg_write(UC_ARM64_REG_X0, arg0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "fopen":
            def bounded_file_string(pointer: int, maximum: int) -> str:
                if pointer == 0:
                    raise ValueError("null guest file string")
                value = bytearray()
                for index in range(maximum + 1):
                    byte = guest.mem_read(pointer + index, 1)[0]
                    if byte == 0:
                        return value.decode("utf-8")
                    value.append(byte)
                raise ValueError("guest file string exceeds diagnostic bound")
            try:
                file_path = bounded_file_string(arg0, 256)
                file_mode = bounded_file_string(arg1, 16)
            except (UcError, UnicodeDecodeError, ValueError) as error:
                file_path = None
                file_mode = None
                reason = str(error)
            else:
                reason = None
            import_call.update({"path": file_path, "mode": file_mode})
            if (diagnose_create_game_main and reason is None
                    and caller == LIBG_CMDLINE_FOPEN_CALL
                    and file_path == "/proc/self/cmdline" and file_mode == "r"
                    and virtual_file["state"] == "unopened"):
                virtual_file["state"] = "open"
                virtual_stdio_calls.append({"symbol": symbol, "caller": hex(caller),
                                            "path": file_path, "mode": file_mode,
                                            "result": hex(VIRTUAL_PROC_FILE),
                                            "source": "pinned engine manifest package",
                                            "argv": [pinned_package],
                                            "content_hex": virtual_cmdline.hex()})
                import_call["virtual_result"] = hex(VIRTUAL_PROC_FILE)
                guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_PROC_FILE)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            cache_read_path = ("/data/user/0/" + pinned_package
                               + "/cache/shaderCache/shaderProgramCache")
            if (diagnose_create_game_main and assets_dir_metadata is not None
                    and reason is None and caller == BASE + 0x012E305C
                    and file_path == cache_read_path and file_mode == "rb"):
                cache_host_path = (guest_fs_root / "data" / "user" / "0" / pinned_package
                                   / "cache" / "shaderCache" / "shaderProgramCache").resolve()
                if (cache_host_path.is_relative_to(guest_fs_root)
                        and not cache_host_path.exists()):
                    import_call.update({"virtual_result": None,
                                        "reason": "shader cache file absent from controlled guest filesystem",
                                        "errno_note": "ENOENT implied by missing file; guest errno not yet exposed"})
                    virtual_stdio_calls.append(import_call.copy())
                    guest.reg_write(UC_ARM64_REG_X0, 0)
                    guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                    return
            if (diagnose_create_game_main and assets_dir_metadata is not None
                    and reason is None and caller == BASE + 0x012E305C
                    and file_path == "fingerprint.json.flex" and file_mode == "rb"):
                guest_working_file = (guest_fs_root / file_path).resolve()
                package_working_file = (guest_fs_root / "data" / "user" / "0"
                                        / pinned_package / file_path).resolve()
                if (guest_working_file.is_relative_to(guest_fs_root)
                        and package_working_file.is_relative_to(guest_fs_root)
                        and not guest_working_file.exists()
                        and not package_working_file.exists()):
                    import_call.update({"virtual_result": None,
                                        "reason": "relative fingerprint flex file absent from controlled guest filesystem",
                                        "errno_note": "ENOENT implied by missing file; guest errno not yet exposed"})
                    virtual_stdio_calls.append(import_call.copy())
                    guest.reg_write(UC_ARM64_REG_X0, 0)
                    guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                    return
            update_fingerprint_path = ("/data/user/0/" + pinned_package
                                       + "/update/fingerprint.json")
            if (diagnose_create_game_main and assets_dir_metadata is not None
                    and reason is None and caller == CPP_BASE + 0xEDBE8
                    and file_path == update_fingerprint_path and file_mode == "wbe"
                    and virtual_update_output["state"] == "unopened"):
                output_path = (guest_fs_root / "data" / "user" / "0" / pinned_package
                               / "update" / "fingerprint.json").resolve()
                if (output_path.is_relative_to(guest_fs_root)
                        and output_path.parent.is_dir() and not output_path.exists()):
                    try:
                        stream = output_path.open("xb")
                    except OSError as error:
                        reason = str(error)
                    else:
                        virtual_update_output.update({"state": "open", "stream": stream,
                                                      "path": output_path})
                        import_call.update({"virtual_result": hex(0x69000100),
                                            "host_path": str(output_path),
                                            "mode_basis": "new guest file; wbe mapped to exclusive binary write"})
                        virtual_stdio_calls.append(import_call.copy())
                        guest.reg_write(UC_ARM64_REG_X0, 0x69000100)
                        guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                        return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "path": file_path, "mode": file_mode,
                              "arguments_x0_x2": [hex(arg0), hex(arg1), hex(arg2)],
                              "reason": reason or "virtual guest fopen is not implemented for this path"})
            guest.emu_stop()
            return
        if symbol == "fgets":
            expected_pipe_read = {
                "/system/bin/which su": (LIBG_WHICH_SU_FGETS_CALL, 0x200),
                "/system/xbin/which su": (LIBG_XBIN_WHICH_SU_FGETS_CALL, 0x100),
            }.get(virtual_which_su_pipe["command"])
            if (diagnose_create_game_main and expected_pipe_read == (caller, arg1)
                    and arg2 == VIRTUAL_WHICH_SU_PIPE
                    and virtual_which_su_pipe["state"] == "open"
                    and STACK <= arg0 <= STACK + STACK_SIZE - arg1):
                virtual_which_su_pipe["state"] = "eof"
                read_call = {"symbol": symbol, "caller": hex(caller),
                             "buffer": hex(arg0), "size": arg1,
                             "file_handle": hex(arg2), "result": "EOF",
                             "reason": "no su executable in controlled guest system image"}
                virtual_stdio_calls.append(read_call)
                import_call.update(read_call)
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            if (not diagnose_create_game_main or caller != LIBG_CMDLINE_FGETS_CALL
                    or virtual_file["state"] != "open" or arg2 != VIRTUAL_PROC_FILE
                    or arg1 != 0x100 or not STACK <= arg0 <= STACK + STACK_SIZE - arg1):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "arguments_x0_x2": [hex(arg0), hex(arg1), hex(arg2)],
                                  "reason": "only the observed single pinned cmdline stack read is supported"})
                guest.emu_stop()
                return
            # fgets copies embedded NUL bytes and adds its own terminating NUL.
            available = virtual_cmdline[virtual_file["cursor"]:]
            newline = available.find(b"\n")
            payload = available[:min(len(available), newline + 1 if newline >= 0 else len(available),
                                     arg1 - 1)]
            if not payload:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "unexpected EOF before the observed cmdline read"})
                guest.emu_stop()
                return
            writable = any(begin <= arg0 and arg0 + len(payload) <= end
                           and permissions & UC_PROT_WRITE
                           for begin, end, permissions in guest.mem_regions())
            if not writable:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "buffer": hex(arg0),
                                  "reason": "guest cmdline buffer is not writable for the bounded read"})
                guest.emu_stop()
                return
            guest.mem_write(arg0, payload + b"\0")
            virtual_file["cursor"] += len(payload)
            virtual_file["state"] = "read"
            read_call = {"symbol": symbol, "caller": hex(caller),
                         "buffer": hex(arg0), "size": arg1,
                         "file_handle": hex(arg2), "payload_hex": payload.hex(),
                         "bytes_written_hex": (payload + b"\0").hex(),
                         "bytes_written_count": len(payload) + 1,
                         "return_buffer": hex(arg0)}
            virtual_stdio_calls.append(read_call)
            import_call.update(read_call)
            guest.reg_write(UC_ARM64_REG_X0, arg0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "fwrite":
            count = arg1 * arg2
            file_handle = guest.reg_read(UC_ARM64_REG_X3)
            if (not diagnose_create_game_main or assets_dir_metadata is None
                    or file_handle != 0x69000100
                    or virtual_update_output["state"] != "open"
                    or count > 16 * 1024 * 1024):
                unhandled.append({**import_call, "size": arg1, "item_count": arg2,
                                  "file_handle": hex(file_handle),
                                  "reason": "fwrite requires bounded data and tracked guest update output"})
                guest.emu_stop()
                return
            try:
                payload = bytes(guest.mem_read(arg0, count)) if count else b""
                written = virtual_update_output["stream"].write(payload)
                if written != count:
                    raise OSError("short write to controlled guest update file")
            except (UcError, OSError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            virtual_update_output["bytes_written"] += written
            virtual_update_output["digest"].update(payload)
            import_call.update({"size": arg1, "item_count": arg2,
                                "file_handle": hex(file_handle),
                                "bytes_written": written,
                                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                                "result": arg2})
            virtual_stdio_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, arg2)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "fflush":
            if (caller != CPP_BASE + 0xEF0F0 or arg0 != 0x69000100
                    or virtual_update_output["state"] != "open"):
                unhandled.append({**import_call, "file_handle": hex(arg0),
                                  "reason": "fflush requires tracked guest update output"})
                guest.emu_stop()
                return
            try:
                virtual_update_output["stream"].flush()
            except OSError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"file_handle": hex(arg0), "result": 0,
                                "bytes_written": virtual_update_output["bytes_written"]})
            virtual_stdio_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "fseeko":
            if (caller != CPP_BASE + 0xEEF28 or arg0 != 0x69000100
                    or arg1 != 0 or arg2 != 1
                    or virtual_update_output["state"] != "open"):
                unhandled.append({**import_call, "file_handle": hex(arg0),
                                  "offset": arg1, "whence": arg2,
                                  "reason": "fseeko requires observed current-position query on tracked output"})
                guest.emu_stop()
                return
            try:
                position = virtual_update_output["stream"].seek(0, 1)
            except OSError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"file_handle": hex(arg0), "offset": 0,
                                "whence": 1, "position": position, "result": 0})
            virtual_stdio_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "ftello":
            if (caller != CPP_BASE + 0xEEF50 or arg0 != 0x69000100
                    or virtual_update_output["state"] != "open"):
                unhandled.append({**import_call, "file_handle": hex(arg0),
                                  "reason": "ftello requires tracked guest update output"})
                guest.emu_stop()
                return
            try:
                position = virtual_update_output["stream"].tell()
            except OSError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"file_handle": hex(arg0), "position": position,
                                "result": position})
            virtual_stdio_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, position)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "fclose":
            if (arg0 == 0x69000100 and virtual_update_output["state"] == "open"):
                try:
                    virtual_update_output["stream"].close()
                except OSError as error:
                    unhandled.append({**import_call, "reason": str(error)})
                    guest.emu_stop()
                    return
                virtual_update_output["state"] = "closed"
                import_call.update({"file_handle": hex(arg0), "result": 0,
                                    "bytes_written": virtual_update_output["bytes_written"],
                                    "content_sha256": virtual_update_output["digest"].hexdigest()})
                virtual_stdio_calls.append(import_call.copy())
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            if (not diagnose_create_game_main or caller != LIBG_CMDLINE_FCLOSE_CALL
                    or arg0 != VIRTUAL_PROC_FILE or virtual_file["state"] != "read"):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "file_handle": hex(arg0),
                                  "reason": "only the observed close of the read pinned cmdline is supported"})
                guest.emu_stop()
                return
            virtual_file["state"] = "closed"
            close_call = {"symbol": symbol, "caller": hex(caller),
                          "file_handle": hex(arg0), "result": 0,
                          "bytes_consumed": virtual_file["cursor"]}
            virtual_stdio_calls.append(close_call)
            import_call.update(close_call)
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "AAssetManager_fromJava" and assets_dir_metadata is not None:
            if (not diagnose_create_game_main or caller != LIBG_ASSET_MANAGER_FROM_JAVA_CALL
                    or arg0 != JNI_ENV or arg1 != JNI_OPAQUE_ASSET_MANAGER
                    or arg1 not in issued_jni_global_refs or virtual_asset_manager_issued):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "arguments_x0_x2": [hex(arg0), hex(arg1), hex(arg2)],
                                  "reason": "only the observed tracked AssetManager conversion is supported"})
                guest.emu_stop()
                return
            virtual_asset_manager_issued = True
            bridge_call = {"symbol": symbol, "caller": hex(caller),
                           "jni_env": hex(arg0), "java_asset_manager": hex(arg1),
                           "guest_asset_manager": hex(VIRTUAL_ASSET_MANAGER),
                           "host_directory": assets_dir_metadata["host_directory"],
                           "fingerprint_version": assets_dir_metadata["fingerprint_version"],
                           "expected_runtime_content_version": assets_dir_metadata[
                               "expected_runtime_content_version"],
                           "version_matches_expected": assets_dir_metadata["version_matches_expected"],
                           "behavior": "opaque manager handle for bounded native asset file reads"}
            asset_manager_bridge_calls.append(bridge_call)
            import_call.update(bridge_call)
            guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_ASSET_MANAGER)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "AAssetManager_open":
            try:
                name_bytes = bytearray()
                if arg1 == 0:
                    raise ValueError("null asset name")
                for index in range(1024):
                    byte = guest.mem_read(arg1 + index, 1)[0]
                    if byte == 0:
                        break
                    name_bytes.append(byte)
                else:
                    raise ValueError("asset name exceeds 1024-byte diagnostic bound")
                asset_name = name_bytes.decode("utf-8", "strict")
                parts = asset_name.split("/")
                if (not asset_name or asset_name.startswith("/") or "\\" in asset_name
                        or ":" in asset_name or any(part in ("", ".", "..") for part in parts)
                        or any(ord(character) < 32 for character in asset_name)):
                    raise ValueError("asset name is not a bounded relative APK path")
                if (not diagnose_create_game_main or assets_dir_metadata is None
                        or not virtual_asset_manager_issued
                        or arg0 != VIRTUAL_ASSET_MANAGER or arg2 not in (0, 1, 2, 3)
                        or not libg_object["text_start"] <= caller <
                        libg_object["text_start"] + libg_object["text_size"]):
                    raise ValueError("unrecognized native asset manager open")
                asset_root = Path(assets_dir_metadata["host_directory"]).resolve(strict=True)
                asset_path = asset_root.joinpath(*parts).resolve()
                if not asset_path.is_relative_to(asset_root):
                    raise ValueError("asset path resolves outside the validated APK assets directory")
                asset_size = asset_path.stat().st_size if asset_path.is_file() else None
                if asset_size is not None and asset_size > (1 << 63) - 1:
                    raise ValueError("asset length exceeds signed 64-bit range")
                if asset_size is not None and len(virtual_assets) >= 256:
                    raise ValueError("too many open diagnostic asset handles")
                stream = asset_path.open("rb") if asset_size is not None else None
            except (UcError, UnicodeDecodeError, ValueError, OSError) as error:
                unhandled.append({**import_call, "manager": hex(arg0),
                                  "name_pointer": hex(arg1), "access_mode": arg2,
                                  "reason": str(error)})
                guest.emu_stop()
                return
            handle = 0
            if stream is not None:
                handle = next_virtual_asset
                next_virtual_asset += 0x10
                virtual_assets[handle] = {"stream": stream, "name": asset_name,
                                          "path": str(asset_path), "size": asset_size}
            import_call.update({"manager": hex(arg0), "name": asset_name,
                                "access_mode": arg2, "host_path": str(asset_path),
                                "exists_in_validated_assets": stream is not None,
                                "asset_bytes": asset_size, "result": hex(handle)})
            asset_manager_bridge_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, handle)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol in ("AAsset_read", "AAsset_seek64", "AAsset_getLength",
                      "AAsset_getLength64", "AAsset_getRemainingLength64", "AAsset_close",
                      "AAsset_openFileDescriptor64"):
            asset = virtual_assets.get(arg0)
            if asset is None:
                unhandled.append({**import_call, "asset_handle": hex(arg0),
                                  "reason": "native asset API requires an open tracked handle"})
                guest.emu_stop()
                return
            stream = asset["stream"]
            import_call.update({"asset_handle": hex(arg0), "name": asset["name"],
                                "host_path": asset["path"]})
            if symbol == "AAsset_openFileDescriptor64":
                evidence = asset_compression_evidence.get(asset["name"])
                if evidence is None:
                    archive_entry = "assets/" + asset["name"]
                    for archive_path in sorted((ROOT / ".local-runtime").glob("prepared-*.apk")):
                        try:
                            with zipfile.ZipFile(archive_path) as archive:
                                info = archive.getinfo(archive_entry)
                                if info.file_size != asset["size"]:
                                    continue
                                archive_digest = hashlib.sha256()
                                with archive.open(info) as archive_stream:
                                    for chunk in iter(lambda: archive_stream.read(1024 * 1024), b""):
                                        archive_digest.update(chunk)
                                extracted_digest = hashlib.sha256()
                                with open(asset["path"], "rb") as extracted_stream:
                                    for chunk in iter(lambda: extracted_stream.read(1024 * 1024), b""):
                                        extracted_digest.update(chunk)
                                if archive_digest.digest() == extracted_digest.digest():
                                    evidence = {"archive": str(archive_path),
                                                "zip_compression_method": info.compress_type,
                                                "content_sha256": archive_digest.hexdigest()}
                                    asset_compression_evidence[asset["name"]] = evidence
                                    break
                        except (OSError, KeyError, zipfile.BadZipFile):
                            continue
                if evidence is None or evidence["zip_compression_method"] != zipfile.ZIP_DEFLATED:
                    unhandled.append({**import_call,
                                      "compression_evidence": evidence,
                                      "reason": "guest file descriptor is supported only for a verified deflated APK asset"})
                    guest.emu_stop()
                    return
                import_call.update({"result": -1,
                                    "compression_evidence": evidence,
                                    "basis": "Android compressed AAsset has no direct file descriptor"})
                asset_manager_bridge_calls.append(import_call.copy())
                guest.reg_write(UC_ARM64_REG_X0, (1 << 64) - 1)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            try:
                if symbol in ("AAsset_getLength", "AAsset_getLength64"):
                    result = asset["size"]
                elif symbol == "AAsset_getRemainingLength64":
                    result = asset["size"] - stream.tell()
                elif symbol == "AAsset_seek64":
                    signed_offset = arg1 if arg1 < (1 << 63) else arg1 - (1 << 64)
                    base_offset = {0: 0, 1: stream.tell(), 2: asset["size"]}.get(arg2)
                    if base_offset is None:
                        raise ValueError("unsupported asset seek origin")
                    next_offset = base_offset + signed_offset
                    if not 0 <= next_offset <= asset["size"]:
                        result = (1 << 64) - 1
                    else:
                        stream.seek(next_offset)
                        result = next_offset
                    import_call.update({"offset": signed_offset, "whence": arg2,
                                        "position": stream.tell()})
                elif symbol == "AAsset_read":
                    read_count = arg2 & 0xFFFFFFFF
                    if read_count and (arg1 == 0 or not any(
                            begin <= arg1 and arg1 + read_count <= end
                            and permissions & UC_PROT_WRITE
                            for begin, end, permissions in guest.mem_regions())):
                        raise ValueError("asset read destination is not writable guest memory")
                    before = stream.tell()
                    payload = stream.read(read_count)
                    if payload:
                        guest.mem_write(arg1, payload)
                    result = len(payload)
                    import_call.update({"destination": hex(arg1), "requested_bytes": read_count,
                                        "position_before": before,
                                        "payload_sha256": hashlib.sha256(payload).hexdigest(),
                                        "position_after": stream.tell()})
                else:
                    stream.close()
                    del virtual_assets[arg0]
                    result = None
            except (UcError, ValueError, OSError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call["result"] = result
            asset_manager_bridge_calls.append(import_call.copy())
            if result is not None:
                guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "ANativeWindow_fromSurface":
            if (not diagnose_create_game_main or caller != BASE + 0x013D1748
                    or arg0 != JNI_ENV or arg1 != JNI_OPAQUE_SURFACE
                    or virtual_native_window["refcount"] != 0):
                unhandled.append({**import_call, "jni_env": hex(arg0),
                                  "surface": hex(arg1),
                                  "reason": "only one observed Surface conversion is supported"})
                guest.emu_stop()
                return
            virtual_native_window["refcount"] = 1
            import_call.update({"jni_env": hex(arg0), "surface": hex(arg1),
                                "window": hex(VIRTUAL_NATIVE_WINDOW),
                                "guest_window_references": 1,
                                "basis": "Windows-owned opaque guest window for bounded lifecycle probe"})
            guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_NATIVE_WINDOW)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "ANativeWindow_release":
            if (caller != BASE + 0x013DAA84 or arg0 != VIRTUAL_NATIVE_WINDOW
                    or virtual_native_window["refcount"] != 1
                    or not virtual_egl["surface_issued"]):
                unhandled.append({**import_call, "window": hex(arg0),
                                  "reason": "unrecognized NativeWindow release"})
                guest.emu_stop()
                return
            virtual_native_window["refcount"] = 0
            import_call.update({"window": hex(arg0), "guest_window_references": 0,
                                "surface_preserved": hex(VIRTUAL_EGL_SURFACE)})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglGetDisplay":
            if (caller != BASE + 0x013985B4 or arg0 != 0
                    or virtual_native_window["refcount"] != 1
                    or virtual_egl["display_issued"]):
                unhandled.append({**import_call, "native_display": hex(arg0),
                                  "reason": "only the first observed default EGL display lookup is supported"})
                guest.emu_stop()
                return
            virtual_egl["display_issued"] = True
            import_call.update({"native_display": "EGL_DEFAULT_DISPLAY",
                                "result": hex(VIRTUAL_EGL_DISPLAY),
                                "basis": "tracked graphics thread after Surface creation"})
            guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_EGL_DISPLAY)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglInitialize":
            def writable_guest_word(pointer: int) -> bool:
                return (pointer != 0 and pointer & 3 == 0
                        and any(begin <= pointer and pointer + 4 <= end
                                and permissions & UC_PROT_WRITE
                                for begin, end, permissions in guest.mem_regions()))
            if (caller != BASE + 0x013985CC or arg0 != VIRTUAL_EGL_DISPLAY
                    or not virtual_egl["display_issued"] or virtual_egl["initialized"]
                    or not writable_guest_word(arg1) or not writable_guest_word(arg2)):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "major_out": hex(arg1), "minor_out": hex(arg2),
                                  "reason": "observed EGL display initialization requires writable version outputs"})
                guest.emu_stop()
                return
            guest.mem_write(arg1, struct.pack("<I", 1))
            guest.mem_write(arg2, struct.pack("<I", 4))
            virtual_egl["initialized"] = True
            import_call.update({"display": hex(arg0), "major_out": hex(arg1),
                                "minor_out": hex(arg2), "version": "1.4", "result": 1})
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglGetConfigs":
            count_out = guest.reg_read(UC_ARM64_REG_X3)
            first_query = (arg1 == 0 and arg2 == 0
                           and virtual_egl["configs_queried"] == 0
                           and caller == BASE + 0x01398200)
            second_query = (virtual_egl["configs_queried"] == 1
                            and caller == BASE + 0x0139823C
                            and arg1 in allocations and allocations[arg1] >= 8
                            and arg2 == 1)
            if (arg0 != VIRTUAL_EGL_DISPLAY or not virtual_egl["initialized"]
                    or not (first_query or second_query) or count_out == 0
                    or count_out & 3 or not any(
                        begin <= count_out and count_out + 4 <= end
                        and permissions & UC_PROT_WRITE
                        for begin, end, permissions in guest.mem_regions())):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "configs_out": hex(arg1), "config_size": arg2,
                                  "count_out": hex(count_out),
                                  "reason": "only the observed two-stage EGL config enumeration is supported"})
                guest.emu_stop()
                return
            guest.mem_write(count_out, struct.pack("<I", 1))
            if second_query:
                guest.mem_write(arg1, struct.pack("<Q", VIRTUAL_EGL_CONFIG))
            virtual_egl["configs_queried"] += 1
            import_call.update({"display": hex(arg0), "configs_out": hex(arg1),
                                "config_size": arg2, "count_out": hex(count_out),
                                "config_count": 1,
                                "config": hex(VIRTUAL_EGL_CONFIG) if second_query else None,
                                "result": 1})
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglGetConfigAttrib":
            attribute_values = {
                0x3021: 8,    # EGL_ALPHA_SIZE
                0x3022: 8,    # EGL_BLUE_SIZE
                0x3023: 8,    # EGL_GREEN_SIZE
                0x3024: 8,    # EGL_RED_SIZE
                0x3025: 24,   # EGL_DEPTH_SIZE
                0x3026: 8,    # EGL_STENCIL_SIZE
                0x3031: 4,    # EGL_SAMPLES
                0x3040: 0x40, # EGL_RENDERABLE_TYPE, OpenGL ES 3 bit
            }
            output = guest.reg_read(UC_ARM64_REG_X3)
            if (caller not in {
                    BASE + offset for offset in (
                        0x01398270, 0x01398284, 0x01398298, 0x01398400,
                        0x01398428, 0x01398450, 0x01398478, 0x013984A0,
                        0x013984D0, 0x01398500, 0x0139861C)}
                    or arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_CONFIG
                    or arg2 not in attribute_values or virtual_egl["configs_queried"] != 2
                    or output == 0 or output & 3 or not any(
                        begin <= output and output + 4 <= end
                        and permissions & UC_PROT_WRITE
                        for begin, end, permissions in guest.mem_regions())):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "config": hex(arg1), "attribute": hex(arg2),
                                  "value_out": hex(output),
                                  "reason": "unrecognized virtual EGL config attribute request"})
                guest.emu_stop()
                return
            value = attribute_values[arg2]
            guest.mem_write(output, struct.pack("<I", value))
            import_call.update({"display": hex(arg0), "config": hex(arg1),
                                "attribute": hex(arg2), "value_out": hex(output),
                                "value": value, "result": 1})
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglChooseConfig":
            attributes = []
            try:
                for index in range(32):
                    attribute = struct.unpack("<I", guest.mem_read(arg1 + index * 8, 4))[0]
                    if attribute == 0x3038:  # EGL_NONE
                        break
                    value = struct.unpack("<I", guest.mem_read(arg1 + index * 8 + 4, 4))[0]
                    attributes.append([hex(attribute), value])
                else:
                    raise ValueError("EGL attribute list exceeds diagnostic bound")
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            expected = [["0x3040", 0x40], ["0x3024", 8], ["0x3023", 8],
                        ["0x3022", 8], ["0x3021", 8], ["0x3025", 24],
                        ["0x3026", 8], ["0x3031", 0]]
            count_out = guest.reg_read(UC_ARM64_REG_X4)
            first_query = (caller == BASE + 0x01398378 and arg2 == 0
                           and arg3 == 0 and virtual_egl["choose_queries"] == 0)
            second_query = (caller == BASE + 0x013983D0
                            and arg2 in allocations and allocations[arg2] >= 8
                            and arg3 == 1 and virtual_egl["choose_queries"] == 1)
            if (arg0 != VIRTUAL_EGL_DISPLAY or attributes != expected
                    or virtual_egl["configs_queried"] != 2
                    or not (first_query or second_query) or count_out == 0
                    or count_out & 3 or not any(
                        begin <= count_out and count_out + 4 <= end
                        and permissions & UC_PROT_WRITE
                        for begin, end, permissions in guest.mem_regions())):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "attributes": attributes, "configs_out": hex(arg2),
                                  "config_size": arg3, "count_out": hex(count_out),
                                  "reason": "observed EGL requirements do not match the virtual config"})
                guest.emu_stop()
                return
            guest.mem_write(count_out, struct.pack("<I", 1))
            if second_query:
                guest.mem_write(arg2, struct.pack("<Q", VIRTUAL_EGL_CONFIG))
            virtual_egl["choose_queries"] += 1
            import_call.update({"display": hex(arg0), "attributes": attributes,
                                "configs_out": hex(arg2), "config_size": arg3,
                                "count_out": hex(count_out), "config_count": 1,
                                "config": hex(VIRTUAL_EGL_CONFIG) if second_query else None,
                                "result": 1})
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglCreateContext":
            try:
                context_attributes = struct.unpack("<IIIII", guest.mem_read(arg3, 20))
            except UcError as error:
                context_attributes = None
                attribute_error = str(error)
            else:
                attribute_error = None
            if (caller != BASE + 0x0139867C
                    or arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_CONFIG
                    or arg2 != 0 or context_attributes != (0x3098, 3, 0x30FB, 0, 0x3038)
                    or virtual_egl["choose_queries"] != 2
                    or virtual_egl["context_issued"]):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "config": hex(arg1), "share_context": hex(arg2),
                                  "attributes": context_attributes,
                                  "reason": attribute_error or
                                  "only the observed first GLES3 context creation is supported"})
                guest.emu_stop()
                return
            virtual_egl["context_issued"] = True
            import_call.update({"display": hex(arg0), "config": hex(arg1),
                                "share_context": hex(arg2),
                                "attributes": list(context_attributes),
                                "context": hex(VIRTUAL_EGL_CONTEXT),
                                "basis": "diagnostic virtual EGL context; no GL implementation"})
            guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_EGL_CONTEXT)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglCreateWindowSurface":
            if (caller != BASE + 0x013DAA78
                    or arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_CONFIG
                    or arg2 != VIRTUAL_NATIVE_WINDOW or arg3 != 0
                    or virtual_native_window["refcount"] != 1
                    or not virtual_egl["context_issued"]
                    or virtual_egl["surface_issued"]):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "config": hex(arg1), "window": hex(arg2),
                                  "attributes": hex(arg3),
                                  "reason": "only the observed Surface-backed EGL window is supported"})
                guest.emu_stop()
                return
            virtual_egl["surface_issued"] = True
            import_call.update({"display": hex(arg0), "config": hex(arg1),
                                "window": hex(arg2), "attributes": None,
                                "surface": hex(VIRTUAL_EGL_SURFACE),
                                "basis": "diagnostic surface identity; no framebuffer"})
            guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_EGL_SURFACE)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglGetCurrentContext":
            if (caller != BASE + 0x01398108
                    or not virtual_egl["surface_issued"]):
                unhandled.append({**import_call,
                                  "reason": "unrecognized EGL current-context query"})
                guest.emu_stop()
                return
            current = virtual_egl["current_context_by_thread"].get(active_guest_thread_id, 0)
            import_call.update({"guest_thread_id": active_guest_thread_id,
                                "result": hex(current)})
            guest.reg_write(UC_ARM64_REG_X0, current)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "eglMakeCurrent":
            query_attempt = (caller == BASE + 0x01398124 and arg3 == 0)
            bind_attempt = (caller == BASE + 0x013980C8
                            and arg3 == VIRTUAL_EGL_CONTEXT)
            if (arg0 != VIRTUAL_EGL_DISPLAY or arg1 != VIRTUAL_EGL_SURFACE
                    or arg2 != VIRTUAL_EGL_SURFACE
                    or not virtual_egl["surface_issued"]
                    or not (query_attempt or bind_attempt)):
                unhandled.append({**import_call, "display": hex(arg0),
                                  "draw": hex(arg1), "read": hex(arg2),
                                  "context": hex(arg3),
                                  "reason": "unrecognized EGL make-current request"})
                guest.emu_stop()
                return
            # EGL_NO_CONTEXT with two non-null surfaces is invalid; the original
            # caller ignores this first return and later binds its real context.
            result = 0 if query_attempt else 1
            if bind_attempt:
                virtual_egl["current_context_by_thread"][active_guest_thread_id] = arg3
            import_call.update({"display": hex(arg0), "draw": hex(arg1),
                                "read": hex(arg2), "context": hex(arg3),
                                "guest_thread_id": active_guest_thread_id,
                                "result": result})
            guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_value_new_breadcrumb":
            def read_breadcrumb_string(pointer: int) -> str:
                if pointer == 0:
                    return ""
                data = bytearray()
                for index in range(513):
                    value = guest.mem_read(pointer + index, 1)[0]
                    if value == 0:
                        return data.decode("utf-8")
                    data.append(value)
                raise ValueError("Sentry breadcrumb string exceeds diagnostic bound")
            try:
                breadcrumb_type = read_breadcrumb_string(arg0)
                breadcrumb_message = read_breadcrumb_string(arg1)
            except (UcError, UnicodeDecodeError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            if (caller != BASE + 0x013CDF04 or not breadcrumb_type
                    or not breadcrumb_message or len(virtual_sentry_values) >= 64):
                unhandled.append({**import_call, "breadcrumb_type": breadcrumb_type,
                                  "breadcrumb_message": breadcrumb_message,
                                  "reason": "unrecognized Sentry breadcrumb request"})
                guest.emu_stop()
                return
            value_handle = 0x68000500 + len(virtual_sentry_values) * 8
            virtual_sentry_values[value_handle] = {
                "type": breadcrumb_type, "message": breadcrumb_message,
                "state": "created"}
            import_call.update({"breadcrumb_type": breadcrumb_type,
                                "breadcrumb_message": breadcrumb_message,
                                "value_handle": hex(value_handle),
                                "behavior": "telemetry value tracked but not sent"})
            telemetry_adapter_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, value_handle)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_value_new_string":
            try:
                raw_value = bytearray()
                for index in range(513):
                    value = guest.mem_read(arg0 + index, 1)[0]
                    if value == 0:
                        break
                    raw_value.append(value)
                else:
                    raise ValueError("Sentry string exceeds diagnostic bound")
                if not raw_value:
                    raise ValueError("Sentry string is empty")
                string_value = raw_value.decode("utf-8")
            except (UcError, UnicodeDecodeError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            if (caller not in (BASE + 0x013CDF1C, BASE + 0x013CDF4C)
                    or (caller == BASE + 0x013CDF4C and string_value != "info")
                    or len(virtual_sentry_values) >= 64):
                unhandled.append({**import_call, "text": string_value,
                                  "reason": "unrecognized Sentry string request"})
                guest.emu_stop()
                return
            value_handle = 0x68000500 + len(virtual_sentry_values) * 8
            virtual_sentry_values[value_handle] = {"type": "string",
                                                    "text": string_value,
                                                    "state": "created"}
            import_call.update({"text": string_value, "value_handle": hex(value_handle),
                                "behavior": "telemetry value tracked but not sent"})
            telemetry_adapter_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, value_handle)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_value_set_by_key":
            breadcrumb = virtual_sentry_values.get(arg0)
            value = virtual_sentry_values.get(arg2)
            try:
                key = bytes(guest.mem_read(arg1, 16)).split(b"\0", 1)[0]
            except UcError:
                key = None
            if ((caller, key) not in ((BASE + 0x013CDF30, b"category"),
                                     (BASE + 0x013CDF60, b"level"))
                    or breadcrumb is None or breadcrumb.get("state") != "created"
                    or value is None or value.get("type") != "string"
                    or value.get("state") != "created"):
                unhandled.append({**import_call, "value_handle": hex(arg0),
                                  "key_pointer": hex(arg1), "child_handle": hex(arg2),
                                  "reason": "unrecognized Sentry breadcrumb key assignment"})
                guest.emu_stop()
                return
            breadcrumb[key.decode("ascii")] = value["text"]
            value["state"] = "moved_into_breadcrumb"
            import_call.update({"value_handle": hex(arg0), "key": key.decode("ascii"),
                                "child_handle": hex(arg2), "text": value["text"],
                                "behavior": "telemetry value tracked but not sent"})
            telemetry_adapter_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_add_breadcrumb":
            breadcrumb = virtual_sentry_values.get(arg0)
            if (caller not in (BASE + 0x013A13C4, BASE + 0x012B8264,
                               BASE + 0x013DAAE8, BASE + 0x012B8440,
                               BASE + 0x012D2DFC, BASE + 0x013A1AD0)
                    or breadcrumb is None
                    or breadcrumb.get("state") != "created"
                    or not all(key in breadcrumb for key in ("type", "message", "category", "level"))):
                unhandled.append({**import_call, "value_handle": hex(arg0),
                                  "reason": "unrecognized Sentry breadcrumb value"})
                guest.emu_stop()
                return
            breadcrumb["state"] = "suppressed"
            import_call.update({"value_handle": hex(arg0),
                                "breadcrumb": breadcrumb.copy(),
                                "behavior": "telemetry suppressed; no remote Sentry call"})
            telemetry_adapter_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_clear_modulecache":
            if caller not in (LIBG_SENTRY_CLEAR_CALL, BASE + 0x013CE094):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "unrecognized Sentry clear-cache callsite"})
                guest.emu_stop()
                return
            telemetry_call = {"symbol": symbol, "caller": hex(caller),
                              "behavior": "telemetry suppressed; original Sentry library not executed"}
            telemetry_adapter_calls.append(telemetry_call)
            import_call["behavior"] = telemetry_call["behavior"]
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_clear_crashed_last_run":
            if caller != BASE + 0x00756C38:
                unhandled.append({**import_call,
                                  "reason": "unrecognized Sentry crash-marker clear callsite"})
                guest.emu_stop()
                return
            import_call["behavior"] = "diagnostic crash telemetry suppressed"
            telemetry_adapter_calls.append(import_call.copy())
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sentry_set_tag":
            if (caller not in (LIBG_SENTRY_SET_TAG_CALL,
                               BASE + 0x013DADE4, BASE + 0x013DAE0C,
                               BASE + 0x013DAE34, BASE + 0x013DAE5C,
                               BASE + 0x00756C70)
                    or arg0 == 0 or arg1 == 0):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "arguments_x0_x1": [hex(arg0), hex(arg1)],
                                  "reason": "unrecognized Sentry set-tag callsite or null tag pointer"})
                guest.emu_stop()
                return
            def read_tag(pointer: int, maximum: int) -> str:
                value = bytearray()
                for index in range(maximum + 1):
                    byte = guest.mem_read(pointer + index, 1)[0]
                    if byte == 0:
                        if not value:
                            raise ValueError("empty Sentry tag component")
                        return value.decode("utf-8")
                    value.append(byte)
                raise ValueError("Sentry tag component exceeds diagnostic bound")
            try:
                key = read_tag(arg0, 128)
                value = read_tag(arg1, 512)
            except (UcError, UnicodeDecodeError, ValueError) as error:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "arguments_x0_x1": [hex(arg0), hex(arg1)],
                                  "reason": str(error)})
                guest.emu_stop()
                return
            telemetry_call = {"symbol": symbol, "caller": hex(caller),
                              "key": key, "value": value,
                              "behavior": "telemetry suppressed; original Sentry library not executed"}
            telemetry_adapter_calls.append(telemetry_call)
            import_call.update({"key": key, "value": value,
                                "behavior": telemetry_call["behavior"]})
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "posix_memalign":
            if (arg0 == 0 or arg0 & 7 or arg1 < 8 or arg1 > PAGE
                    or arg1 & (arg1 - 1) or not 0 < arg2 <= 16 * 1024 * 1024):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "arguments_x0_x2": [hex(arg0), hex(arg1), hex(arg2)],
                                  "reason": "unsupported output pointer, alignment, or allocation size"})
                guest.emu_stop()
                return
            writable = any(begin <= arg0 and arg0 + 7 <= end and permissions & UC_PROT_WRITE
                           for begin, end, permissions in guest.mem_regions())
            if not writable:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "output_pointer": hex(arg0),
                                  "reason": "guest aligned-allocation output is not writable"})
                guest.emu_stop()
                return
            address = allocate(arg2, max(16, arg1))
            if address == 0:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "guest diagnostic heap cannot satisfy aligned allocation"})
                guest.emu_stop()
                return
            try:
                guest.mem_write(arg0, struct.pack("<Q", address))
            except UcError:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "output_pointer": hex(arg0),
                                  "reason": "guest aligned-allocation output write failed"})
                guest.emu_stop()
                return
            import_call["alignment"] = arg1
            import_call["size"] = arg2
            import_call["allocation"] = hex(address)
            import_call["result"] = 0
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "__strlen_chk":
            if arg0 == 0 or not 0 < arg1 <= 4096:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "pointer": hex(arg0), "object_size": arg1,
                                  "reason": "null pointer or unsupported FORTIFY object bound"})
                guest.emu_stop()
                return
            try:
                bounded = bytes(guest.mem_read(arg0, arg1))
            except UcError:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "pointer": hex(arg0), "object_size": arg1,
                                  "reason": "FORTIFY object is not fully mapped"})
                guest.emu_stop()
                return
            end = bounded.find(b"\0")
            if end < 0:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "pointer": hex(arg0), "object_size": arg1,
                                  "bytes_within_bound_hex": bounded.hex(),
                                  "reason": "Bionic FORTIFY would abort: no NUL within object size"})
                guest.emu_stop()
                return
            import_call.update({"pointer": hex(arg0), "object_size": arg1,
                                "bytes_through_nul_hex": bounded[:end + 1].hex(),
                                "result": end})
            guest.reg_write(UC_ARM64_REG_X0, end)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "strnlen":
            if arg0 == 0 or not 0 <= arg1 <= 16 * 1024 * 1024:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "pointer": hex(arg0), "maximum": arg1,
                                  "reason": "null pointer or unsupported string bound"})
                guest.emu_stop()
                return
            try:
                bounded = bytes(guest.mem_read(arg0, arg1)) if arg1 else b""
            except UcError:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "pointer": hex(arg0), "maximum": arg1,
                                  "reason": "bounded string is not fully mapped"})
                guest.emu_stop()
                return
            terminator = bounded.find(b"\0")
            length = terminator if terminator >= 0 else arg1
            import_call.update({"pointer": hex(arg0), "maximum": arg1,
                                "result": length, "nul_found": terminator >= 0})
            guest.reg_write(UC_ARM64_REG_X0, length)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "atoi":
            if caller != BASE + 0x0123552C or arg0 == 0:
                unhandled.append({**import_call, "pointer": hex(arg0),
                                  "reason": "unrecognized atoi callsite or null input"})
                guest.emu_stop()
                return
            try:
                data = bytearray()
                for index in range(64):
                    value = guest.mem_read(arg0 + index, 1)[0]
                    if value == 0:
                        break
                    if value > 0x7F:
                        raise ValueError("atoi input is not ASCII")
                    data.append(value)
                else:
                    raise ValueError("atoi input exceeds 64-byte diagnostic bound")
                match = re.match(rb"[\t\n\v\f\r ]*([+-]?[0-9]+)", bytes(data))
                result = int(match.group(1)) if match else 0
                if not -(1 << 31) <= result < (1 << 31):
                    raise ValueError("atoi result exceeds signed int range")
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"input": bytes(data).decode("ascii"), "result": result})
            guest.reg_write(UC_ARM64_REG_X0, result & 0xFFFFFFFFFFFFFFFF)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "atof":
            if caller != BASE + 0x01235B00 or arg0 == 0:
                unhandled.append({**import_call, "pointer": hex(arg0),
                                  "reason": "unrecognized atof callsite or null input"})
                guest.emu_stop()
                return
            try:
                data = bytearray()
                for index in range(64):
                    value = guest.mem_read(arg0 + index, 1)[0]
                    if value == 0:
                        break
                    if value > 0x7F:
                        raise ValueError("atof input is not ASCII")
                    data.append(value)
                else:
                    raise ValueError("atof input exceeds 64-byte diagnostic bound")
                match = re.match(
                    rb"[\t\n\v\f\r ]*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)",
                    bytes(data))
                if match is None:
                    raise ValueError("atof input has no supported decimal prefix")
                result = float(match.group(1))
            except (UcError, ValueError, OverflowError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"input": bytes(data).decode("ascii"), "result": result,
                                "basis": "ASCII decimal parsed to IEEE-754 binary64"})
            guest.reg_write(UC_ARM64_REG_D0, struct.unpack(
                "<Q", struct.pack("<d", result))[0])
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "clock_gettime":
            if (arg0 not in (0, 1) or (arg0 == 0 and caller != CPP_BASE + 0xCCA14)
                    or arg1 == 0 or arg1 & 7):
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "clock_id": arg0, "timespec_pointer": hex(arg1),
                                  "reason": "only observed aligned virtual timespec queries are supported"})
                guest.emu_stop()
                return
            now = virtual_clock["wall_ns" if arg0 == 0 else "monotonic_ns"]
            try:
                guest.mem_write(arg1, struct.pack("<qq", *divmod(now, 1_000_000_000)))
            except UcError:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "timespec_pointer": hex(arg1),
                                  "reason": "guest timespec is unmapped or unwritable"})
                guest.emu_stop()
                return
            import_call["clock_id"] = "CLOCK_REALTIME" if arg0 == 0 else "CLOCK_MONOTONIC"
            import_call["virtual_nanoseconds"] = now
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "time":
            if arg0 != 0:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "time_pointer": hex(arg0),
                                  "reason": "only time(null) is supported"})
                guest.emu_stop()
                return
            wall_seconds = virtual_clock["wall_ns"] // 1_000_000_000
            import_call["virtual_wall_seconds"] = wall_seconds
            guest.reg_write(UC_ARM64_REG_X0, wall_seconds)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_key_create":
            allowed_destructor = (arg1 == 0 or
                                  (arg1 == CPP_BASE + 0xE1650 and caller == CPP_BASE + 0xE121C) or
                                  (arg1 == CPP_BASE + 0x12DD40 and caller == CPP_BASE + 0x12DD1C))
            if arg0 == 0 or arg0 & 3 or not allowed_destructor or next_thread_key_index >= 128:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "key_pointer": hex(arg0), "destructor": hex(arg1),
                                  "reason": "only observed key destructor and aligned output are supported"})
                guest.emu_stop()
                return
            guest_key = 0x80000000 | next_thread_key_index
            try:
                guest.mem_write(arg0, struct.pack("<I", guest_key))
            except UcError:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "key_pointer": hex(arg0),
                                  "reason": "guest key output is unmapped or unwritable"})
                guest.emu_stop()
                return
            thread_keys[guest_key] = arg1
            next_thread_key_index += 1
            import_call["guest_key"] = hex(guest_key)
            import_call["destructor"] = hex(arg1)
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_setspecific":
            if arg0 not in thread_keys or arg1 == 0:
                unhandled.append({**import_call, "key": hex(arg0), "value": hex(arg1),
                                  "reason": "setspecific requires a tracked key and nonnull value"})
                guest.emu_stop()
                return
            try:
                guest.mem_read(arg1, 1)
            except UcError as error:
                unhandled.append({**import_call, "key": hex(arg0), "value": hex(arg1),
                                  "reason": str(error)})
                guest.emu_stop()
                return
            thread_key_values.setdefault(active_guest_thread_id, {})[arg0] = arg1
            import_call.update({"key": hex(arg0), "value": hex(arg1),
                                "guest_thread_id": active_guest_thread_id, "result": 0})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_getspecific":
            if arg0 not in thread_keys:
                unhandled.append({**import_call, "key": hex(arg0),
                                  "reason": "getspecific requires a tracked key"})
                guest.emu_stop()
                return
            value = thread_key_values.get(active_guest_thread_id, {}).get(arg0, 0)
            import_call.update({"key": hex(arg0), "value": hex(value),
                                "guest_thread_id": active_guest_thread_id})
            guest.reg_write(UC_ARM64_REG_X0, value)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "dlsym":
            try:
                lookup_name = bytes(guest.mem_read(arg1, 128)).split(b"\0", 1)[0]
            except UcError:
                lookup_name = b""
            if arg0 != 0 or lookup_name != b"dl_iterate_phdr":
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "handle": hex(arg0),
                                  "lookup_name": lookup_name.decode("ascii", errors="replace"),
                                  "reason": "only the observed dl_iterate_phdr lookup is supported"})
                guest.emu_stop()
                return
            import_call["lookup_name"] = "dl_iterate_phdr"
            import_call["result"] = hex(DL_ITERATE_PHDR)
            guest.reg_write(UC_ARM64_REG_X0, DL_ITERATE_PHDR)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_condattr_init":
            condition_call = {"symbol": symbol, "attr_pointer": hex(arg0), "caller": hex(caller)}
            condition_adapter_calls.append(condition_call)
            if arg0 == 0 or arg0 & 7 or arg0 in initialized_cond_attrs:
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "invalid or already initialized condition attribute"})
                guest.emu_stop()
                return
            try:
                guest.mem_write(arg0, struct.pack("<Q", 0))
            except UcError:
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "guest condition attribute is unmapped or unwritable"})
                guest.emu_stop()
                return
            initialized_cond_attrs.add(arg0)
            condition_call["result"] = "ok"
            condition_call["guest_value"] = 0
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_condattr_setclock":
            condition_call = {"symbol": symbol, "attr_pointer": hex(arg0),
                              "clock_id": arg1, "caller": hex(caller)}
            condition_adapter_calls.append(condition_call)
            if arg0 not in initialized_cond_attrs or arg1 != 1:
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "only tracked CLOCK_MONOTONIC condition attribute is supported"})
                guest.emu_stop()
                return
            try:
                before = struct.unpack("<Q", guest.mem_read(arg0, 8))[0]
                if before != 0:
                    raise ValueError("condition attribute is not the private realtime default")
                guest.mem_write(arg0, struct.pack("<Q", 2))
            except (UcError, ValueError):
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "guest condition attribute is invalid or unwritable"})
                guest.emu_stop()
                return
            condition_call["result"] = "ok"
            condition_call["guest_value"] = 2
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_cond_init":
            condition_call = {"symbol": symbol, "condition_pointer": hex(arg0),
                              "attr_pointer": hex(arg1), "caller": hex(caller)}
            condition_adapter_calls.append(condition_call)
            if arg0 == 0 or arg0 & 3 or arg1 not in initialized_cond_attrs:
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "only aligned condition with tracked attr is supported"})
                guest.emu_stop()
                return
            try:
                attr_value = struct.unpack("<Q", guest.mem_read(arg1, 8))[0]
                if attr_value != 2:
                    raise ValueError("condition attr is not private monotonic")
                original = bytes(guest.mem_read(arg0, 48))
                guest.mem_write(arg0, original)  # Validate the full Bionic LP64 object is writable.
                guest.mem_write(arg0, struct.pack("<II", 2, 0))
            except (UcError, ValueError):
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "condition or attr guest memory is invalid"})
                guest.emu_stop()
                return
            condition_waiters[arg0] = 0
            condition_call["result"] = "ok"
            condition_call["guest_state"] = 2
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_cond_destroy":
            condition_call = {"symbol": symbol, "condition_pointer": hex(arg0),
                              "caller": hex(caller),
                              "registered_waiters": condition_waiters.get(arg0, 0)}
            condition_adapter_calls.append(condition_call)
            try:
                raw_condition = bytes(guest.mem_read(arg0, 48))
                state, guest_waiters = struct.unpack_from("<II", raw_condition)
            except UcError as error:
                raw_condition = None
                state, guest_waiters = None, None
                raw_error = str(error)
            else:
                raw_error = None
            condition_call.update({"raw_state": (raw_condition[:8].hex()
                                                 if raw_condition is not None else None),
                                   "state": state, "guest_waiters": guest_waiters})
            if (raw_error is not None or arg0 == 0 or arg0 & 3
                    or state not in (0, 2) or guest_waiters != 0
                    or condition_call["registered_waiters"] != 0
                    or condition_wait_queue.get(arg0)):
                condition_call.update({"result": "stopped", "reason": raw_error or
                                       "condition destroy requires an aligned unused condition"})
                unhandled.append(condition_call.copy())
                guest.emu_stop()
                return
            guest.mem_write(arg0, struct.pack("<I", 0xDEADC04D))
            condition_waiters.pop(arg0, None)
            condition_wait_queue.pop(arg0, None)
            condition_call.update({"result": "ok", "guest_destroyed_state": "0xdeadc04d"})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_condattr_destroy":
            condition_call = {"symbol": symbol, "attr_pointer": hex(arg0), "caller": hex(caller)}
            condition_adapter_calls.append(condition_call)
            if arg0 not in initialized_cond_attrs:
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "condition attr was not initialized"})
                guest.emu_stop()
                return
            try:
                attr_value = struct.unpack("<Q", guest.mem_read(arg0, 8))[0]
                if attr_value != 2:
                    raise ValueError("condition attr is not private monotonic")
                guest.mem_write(arg0, struct.pack("<Q", 0xDEADA11D))
            except (UcError, ValueError):
                condition_call["result"] = "stopped"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "condition attr guest memory is invalid"})
                guest.emu_stop()
                return
            initialized_cond_attrs.remove(arg0)
            condition_call["result"] = "ok"
            condition_call["guest_value"] = hex(0xDEADA11D)
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_mutexattr_destroy":
            attr_call = {"symbol": symbol, "attr_pointer": hex(arg0), "caller": hex(caller)}
            mutex_adapter_calls.append(attr_call)

            def attr_destroy_failure(reason: str) -> None:
                attr_call["result"] = "stopped"
                attr_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "attr_pointer": hex(arg0), "reason": reason})
                guest.emu_stop()

            if arg0 == 0 or arg0 & 7 or arg0 not in initialized_mutex_attrs:
                attr_destroy_failure("requires a tracked initialized 8-byte mutex attribute")
                return
            try:
                prior_type = struct.unpack("<Q", bytes(guest.mem_read(arg0, 8)))[0]
                if prior_type not in (0, 1):
                    attr_destroy_failure("guest mutex attribute contains unknown flags")
                    return
                guest.mem_write(arg0, struct.pack("<Q", 0xFFFFFFFFFFFFFFFF))
            except UcError:
                attr_destroy_failure("guest mutex attribute pointer is unmapped or unwritable")
                return
            initialized_mutex_attrs.remove(arg0)
            attr_call["prior_type"] = prior_type
            attr_call["guest_destroyed_value"] = hex(0xFFFFFFFFFFFFFFFF)
            attr_call["result"] = "ok"
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_mutexattr_settype":
            attr_call = {"symbol": symbol, "attr_pointer": hex(arg0), "caller": hex(caller),
                         "requested_type": arg1}
            mutex_adapter_calls.append(attr_call)

            def settype_failure(reason: str) -> None:
                attr_call["result"] = "stopped"
                attr_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "attr_pointer": hex(arg0), "requested_type": arg1,
                                  "reason": reason})
                guest.emu_stop()

            if arg0 == 0 or arg0 & 7 or arg0 not in initialized_mutex_attrs or arg1 != 1:
                settype_failure("requires an initialized 8-byte attribute and recursive type 1")
                return
            try:
                prior_type = struct.unpack("<Q", bytes(guest.mem_read(arg0, 8)))[0]
                if prior_type not in (0, 1):
                    settype_failure("guest mutex attribute contains unknown flags")
                    return
                guest.mem_write(arg0, struct.pack("<Q", 1))
            except UcError:
                settype_failure("guest mutex attribute pointer is unmapped or unwritable")
                return
            attr_call["prior_type"] = prior_type
            attr_call["result"] = "ok"
            attr_call["guest_value"] = 1
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_mutexattr_init":
            attr_call = {"symbol": symbol, "attr_pointer": hex(arg0), "caller": hex(caller)}
            mutex_adapter_calls.append(attr_call)

            def attr_failure(reason: str) -> None:
                attr_call["result"] = "stopped"
                attr_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "attr_pointer": hex(arg0), "reason": reason})
                guest.emu_stop()

            if arg0 == 0 or arg0 & 7:
                attr_failure("null or non-8-byte-aligned guest mutex attribute pointer")
                return
            if arg0 in initialized_mutex_attrs:
                attr_failure("guest mutex attributes were already initialized")
                return
            try:
                original = bytes(guest.mem_read(arg0, 8))
                guest.mem_write(arg0, original)  # Check this mapped guest address is writable.
                guest.mem_write(arg0, struct.pack("<Q", 0))
            except UcError:
                attr_failure("guest mutex attribute pointer is unmapped or unwritable")
                return
            initialized_mutex_attrs.add(arg0)
            attr_call["result"] = "ok"
            attr_call["guest_default_value"] = 0
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "__ctype_get_mb_cur_max":
            locale_call = {"symbol": symbol, "caller": hex(caller),
                           "current_locale": (hex(guest_thread_locale)
                                              if guest_thread_locale is not None else "LC_GLOBAL_LOCALE")}
            locale_adapter_calls.append(locale_call)

            def mb_cur_max_failure(reason: str) -> None:
                locale_call["result"] = "stopped"
                locale_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller), "reason": reason,
                                  "current_locale": locale_call["current_locale"]})
                guest.emu_stop()

            if guest_thread_locale not in locale_handles:
                mb_cur_max_failure("current locale is not a tracked C locale handle")
                return
            try:
                mb_cur_max = struct.unpack("<Q", bytes(guest.mem_read(guest_thread_locale, 8)))[0]
            except UcError:
                mb_cur_max_failure("tracked C locale object is unmapped")
                return
            locale_call["guest_mb_cur_max"] = mb_cur_max
            if mb_cur_max != 1:
                mb_cur_max_failure("tracked C locale object has unexpected mb_cur_max")
                return
            locale_call["result"] = 1
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "mbtowc":
            locale_call = {"symbol": symbol, "caller": hex(caller), "target": hex(arg0),
                           "source": hex(arg1), "n": arg2,
                           "current_locale": (hex(guest_thread_locale)
                                              if guest_thread_locale is not None else "LC_GLOBAL_LOCALE")}
            locale_adapter_calls.append(locale_call)
            if arg0 != 0 or arg1 != 0 or guest_thread_locale not in locale_handles:
                locale_call["result"] = "stopped"
                locale_call["reason"] = "only reset/query with null pointers under tracked C locale is supported"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "target": hex(arg0), "source": hex(arg1), "n": arg2,
                                  "reason": locale_call["reason"]})
                guest.emu_stop()
                return
            locale_call["result"] = 0
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "uselocale":
            locale_call = {"symbol": symbol, "caller": hex(caller), "requested": hex(arg0)}
            locale_adapter_calls.append(locale_call)
            if arg0 not in (0, 0xFFFFFFFFFFFFFFFF) and arg0 not in locale_handles:
                locale_call["result"] = "stopped"
                locale_call["reason"] = "unknown guest locale handle"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "requested": hex(arg0), "reason": "unknown guest locale handle"})
                guest.emu_stop()
                return
            previous = (guest_thread_locale if guest_thread_locale is not None
                        else 0xFFFFFFFFFFFFFFFF)
            if arg0 != 0:
                guest_thread_locale = None if arg0 == 0xFFFFFFFFFFFFFFFF else arg0
            locale_call["result_previous_locale"] = hex(previous)
            locale_call["current_locale"] = (hex(guest_thread_locale)
                                             if guest_thread_locale is not None else "LC_GLOBAL_LOCALE")
            guest.reg_write(UC_ARM64_REG_X0, previous)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "newlocale":
            locale_call = {"symbol": symbol, "caller": hex(caller), "mask": hex(arg0),
                           "name_pointer": hex(arg1), "base_locale": hex(arg2)}
            locale_adapter_calls.append(locale_call)

            def locale_failure(reason: str) -> None:
                locale_call["result"] = "stopped"
                locale_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller), "reason": reason,
                                  "mask": hex(arg0), "name_pointer": hex(arg1),
                                  "base_locale": hex(arg2)})
                guest.emu_stop()

            if arg0 not in (0x1FBF, 0x6) or arg1 == 0 or arg2 != 0:
                locale_failure('only observed newlocale masks 0x1fbf/0x6 with "C" and null base are supported')
                return
            try:
                locale_name = bytes(guest.mem_read(arg1, 2))
            except UcError:
                locale_failure("guest locale name pointer is unmapped")
                return
            locale_call["name"] = locale_name.decode("ascii", errors="replace")
            if locale_name != b"C\0":
                locale_failure("guest locale name is not exactly C")
                return
            locale_handle = allocate(8)
            if locale_handle == 0:
                locale_failure("guest diagnostic heap exhausted")
                return
            guest.mem_write(locale_handle, struct.pack("<Q", 1))
            locale_handles.add(locale_handle)
            locale_call["result"] = hex(locale_handle)
            locale_call["guest_mb_cur_max"] = 1
            guest.reg_write(UC_ARM64_REG_X0, locale_handle)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol in ("pthread_cond_signal", "pthread_cond_broadcast"):
            condition_call = {"symbol": symbol, "condition_pointer": hex(arg0), "caller": hex(caller),
                              "registered_waiters": condition_waiters.get(arg0, 0)}
            condition_adapter_calls.append(condition_call)

            def condition_failure(reason: str) -> None:
                condition_call["result"] = "stopped"
                condition_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "condition_pointer": hex(arg0), "reason": reason})
                guest.emu_stop()

            if arg0 == 0 or arg0 & 3:
                condition_failure("null or unaligned guest condition pointer")
                return
            try:
                raw_state = bytes(guest.mem_read(arg0, 8))
            except UcError:
                condition_failure("guest condition pointer is unmapped")
                return
            state_value, guest_waiters = struct.unpack("<II", raw_state)
            condition_call["raw_state"] = raw_state.hex()
            if (guest_waiters or state_value not in (0, 2)
                    or condition_call["registered_waiters"] != len(
                        condition_wait_queue.get(arg0, []))):
                condition_failure("condition state differs from tracked guest wait queue")
                return
            queue = condition_wait_queue.setdefault(arg0, [])
            wake_count = min(len(queue), 1) if symbol == "pthread_cond_signal" else len(queue)
            awakened = [queue.pop(0) for _ in range(wake_count)]
            for handle in awakened:
                worker = guest_threads[handle]
                if worker["state"] != "waiting_condition" or worker["wait_condition"] != arg0:
                    condition_failure("signaled guest worker was not waiting on this condition")
                    return
                worker["state"] = "runnable_after_wait"
                worker["wait_return_value"] = 0
            condition_waiters[arg0] = len(queue)
            condition_call["awakened_guest_threads"] = [hex(handle) for handle in awakened]
            condition_call["result"] = "woke tracked waiters" if awakened else "no_waiters_noop"
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "syscall":
            syscall_call = {"number": arg0, "caller": hex(caller),
                            "arguments_x1_x2": [hex(arg1), hex(arg2)]}
            syscall_adapter_calls.append(syscall_call)
            if arg0 != 178:
                syscall_call["result"] = "stopped"
                syscall_call["reason"] = "unsupported syscall number"
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "number": arg0, "reason": "unsupported syscall number"})
                guest.emu_stop()
                return
            syscall_call["result"] = active_guest_thread_id
            guest.reg_write(UC_ARM64_REG_X0, active_guest_thread_id)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "sysconf":
            virtual_result = {
                (0x61, CPP_BASE + 0xE10D8): ("_SC_NPROCESSORS_ONLN", 1),
                (0x94, BASE + 0x01392510): ("_SC_LEVEL1_DCACHE_LINESIZE", 64),
                (0x27, BASE + 0x012E78C0): ("_SC_PAGESIZE", PAGE),
            }.get((arg0, caller))
            if virtual_result is None:
                unhandled.append({**import_call, "name": hex(arg0),
                                  "reason": "only observed virtual CPU/cache queries are supported"})
                guest.emu_stop()
                return
            name, result = virtual_result
            import_call.update({"name": name, "result": result,
                                "basis": "controlled virtual guest CPU/cache configuration"})
            guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_self":
            current_handle = (GUEST_PTHREAD_SELF if active_guest_thread_id == GUEST_THREAD_ID
                              else next((handle for handle, worker in guest_threads.items()
                                         if worker["thread_id"] == active_guest_thread_id), None))
            if diagnose_create_game_main and current_handle is not None:
                import_call.update({"result": hex(current_handle),
                                    "guest_thread_id": active_guest_thread_id,
                                    "basis": "tracked guest thread context"})
                guest.reg_write(UC_ARM64_REG_X0, current_handle)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "reason": "pthread_self requires a tracked guest thread"})
            guest.emu_stop()
            return
        if symbol == "pthread_once":
            try:
                raw_once = bytes(guest.mem_read(arg0, 4))
            except UcError as error:
                raw_once = None
                raw_error = str(error)
            else:
                raw_error = None
            import_call.update({"control": hex(arg0), "init_routine": hex(arg1),
                                "raw_state": raw_once.hex() if raw_once is not None else None})
            if (raw_error is not None or caller != CPP_BASE + 0x12DB74
                    or arg0 != CPP_BASE + 0x14E2C8
                    or arg1 != CPP_BASE + 0x12DD04 or once_pending):
                unhandled.append({**import_call, "reason": raw_error or
                                  "only the observed nonrecursive libc++ once callback is supported"})
                guest.emu_stop()
                return
            state = struct.unpack("<I", raw_once)[0]
            if state == 2 and arg0 in completed_once_controls:
                import_call["result"] = 0
                import_call["state"] = "completed"
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            if state != 0 or arg0 in completed_once_controls:
                unhandled.append({**import_call,
                                  "reason": "once control is already running or inconsistent"})
                guest.emu_stop()
                return
            return_address = guest.reg_read(UC_ARM64_REG_X30)
            guest.mem_write(arg0, struct.pack("<I", 1))
            once_pending.update({"control": arg0, "return_address": return_address,
                                 "guest_thread_id": active_guest_thread_id})
            import_call["state"] = "running original initializer"
            guest.reg_write(UC_ARM64_REG_X30, PTHREAD_ONCE_RETURN)
            guest.reg_write(UC_ARM64_REG_PC, arg1)
            return
        if symbol == "vsnprintf":
            if (caller == BASE + 0x0138D15C
                    and arg2 == BASE + 0x00331DFE
                    and arg1 == 1024
                    and WORKER_STACK <= arg0 <= WORKER_STACK + WORKER_STACK_SIZE - arg1
                    and WORKER_STACK <= guest.reg_read(UC_ARM64_REG_X3)
                    <= WORKER_STACK + WORKER_STACK_SIZE - 32):
                format_bytes = b"Trying to create sc3dOpenglContext(GLES3)."
                try:
                    if bytes(guest.mem_read(arg2, len(format_bytes) + 1)) != format_bytes + b"\0":
                        raise ValueError("guest OpenGL diagnostic format differs from pinned binary")
                    guest.mem_read(guest.reg_read(UC_ARM64_REG_X3), 32)
                    guest.mem_write(arg0, format_bytes + b"\0")
                except (UcError, ValueError) as error:
                    unhandled.append({**import_call, "reason": str(error)})
                    guest.emu_stop()
                    return
                import_call.update({"format": format_bytes.decode("ascii"),
                                    "rendered": format_bytes.decode("ascii"),
                                    "result": len(format_bytes)})
                guest.reg_write(UC_ARM64_REG_X0, len(format_bytes))
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            try:
                format_bytes = bytes(guest.mem_read(arg2, 256)).split(b"\0", 1)[0]
                va_words = bytes(guest.mem_read(guest.reg_read(UC_ARM64_REG_X3), 32))
                stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                    "<QQQii", va_words)
                first_argument = gr_top + gr_offs
                string_pointer = struct.unpack("<Q", guest.mem_read(first_argument, 8))[0]
                decimal_value = struct.unpack("<i", guest.mem_read(first_argument + 8, 4))[0]
                string_bytes = bytes(guest.mem_read(string_pointer, 128)).split(b"\0", 1)[0]
                detail_error = None
            except UcError as error:
                format_bytes = b""
                va_words = b""
                string_pointer = 0
                decimal_value = None
                string_bytes = b""
                detail_error = str(error)
            details = {"destination": hex(arg0), "size": arg1,
                       "format_pointer": hex(arg2),
                       "va_list_pointer": hex(guest.reg_read(UC_ARM64_REG_X3)),
                       "format": format_bytes.decode("ascii", errors="replace"),
                       "va_list_raw": va_words.hex(),
                       "string_pointer": hex(string_pointer),
                       "string_argument": string_bytes.decode("ascii", errors="replace"),
                       "decimal_argument": decimal_value}
            if (detail_error is None and caller == BASE + 0x0138D15C
                    and arg2 == BASE + 0x002C78B6
                    and format_bytes == b"%s/%s"
                    and string_bytes == ("/data/user/0/" + pinned_package
                                         + "/update").encode("ascii")
                    and gr_offs == -56 and vr_offs == -128
                    and arg1 == 1024 and WORKER_STACK <= arg0
                    <= WORKER_STACK + WORKER_STACK_SIZE - arg1):
                try:
                    second_pointer = struct.unpack(
                        "<Q", guest.mem_read(first_argument + 8, 8))[0]
                    second_raw = bytes(guest.mem_read(second_pointer, 256))
                    terminator = second_raw.find(b"\0")
                    if terminator < 0:
                        raise ValueError("second path component exceeds 256 bytes")
                    second_string = second_raw[:terminator].decode("utf-8")
                    rendered = string_bytes + b"/" + second_string.encode("utf-8")
                    if len(rendered) >= arg1:
                        raise ValueError("formatted update path exceeds destination")
                    guest.mem_write(arg0, rendered + b"\0")
                except (UcError, UnicodeDecodeError, ValueError) as error:
                    unhandled.append({**import_call, **details, "reason": str(error)})
                    guest.emu_stop()
                    return
                import_call.update({**details, "second_string_pointer": hex(second_pointer),
                                    "second_string": second_string,
                                    "rendered": rendered.decode("utf-8"),
                                    "result": len(rendered)})
                guest.reg_write(UC_ARM64_REG_X0, len(rendered))
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            if (detail_error is None and caller == BASE + 0x0138D15C
                    and arg2 == BASE + 0x0033EF1F
                    and format_bytes == b"Created sc3d context, backend=%s (value=%d)."
                    and string_pointer == BASE + 0x002BA3D2
                    and string_bytes == b"OpenGLES3"
                    and gr_offs == -56 and vr_offs == -128
                    and arg1 == 1024 and WORKER_STACK <= arg0
                    <= WORKER_STACK + WORKER_STACK_SIZE - arg1):
                rendered = (b"Created sc3d context, backend=" + string_bytes
                            + b" (value=" + str(decimal_value).encode("ascii") + b").")
                guest.mem_write(arg0, rendered + b"\0")
                import_call.update({**details, "result": len(rendered),
                                    "rendered": rendered.decode("ascii")})
                guest.reg_write(UC_ARM64_REG_X0, len(rendered))
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            if (detail_error is not None or caller != BASE + 0x0138D15C
                    or format_bytes != b"%s-%d" or string_pointer != BASE + 0x002BA40A
                    or string_bytes != b"worker" or decimal_value not in (0, 1)
                    or gr_offs != -56 or vr_offs != -128
                    or arg1 != 1024 or not WORKER_STACK <= arg0
                    <= WORKER_STACK + WORKER_STACK_SIZE - arg1):
                unhandled.append({**import_call, **details, "reason": detail_error or
                                  "only observed worker-0/1 vsnprintf format is supported"})
                guest.emu_stop()
                return
            rendered = f"worker-{decimal_value}".encode("ascii")
            guest.mem_write(arg0, rendered + b"\0")
            import_call.update({**details, "result": len(rendered),
                                "rendered": rendered.decode("ascii")})
            guest.reg_write(UC_ARM64_REG_X0, len(rendered))
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_create":
            start_argument = guest.reg_read(UC_ARM64_REG_X3)
            try:
                argument_words = struct.unpack("<2Q", guest.mem_read(start_argument, 16))
                extended_argument_words = (struct.unpack("<4Q", guest.mem_read(start_argument, 32))
                                           if caller in (BASE + 0x0139E268,
                                                         BASE + 0x013170D8) else None)
                sc3d_argument_words = (struct.unpack("<3Q", guest.mem_read(start_argument, 24))
                                       if caller == BASE + 0x012D2DA4 else None)
                raw_error = None
            except UcError as error:
                argument_words = None
                extended_argument_words = None
                sc3d_argument_words = None
                raw_error = str(error)
            import_call.update({"thread_out": hex(arg0), "attributes": hex(arg1),
                                "start_routine": hex(arg2),
                                "start_argument": hex(start_argument),
                                "start_argument_words": ([hex(value) for value in
                                                          (extended_argument_words or
                                                           sc3d_argument_words or argument_words)]
                                                         if argument_words is not None else None),
                                "tracked_allocation_sizes": (
                                    [allocations.get(start_argument),
                                     allocations.get(argument_words[0]),
                                     allocations.get(argument_words[1])]
                                    if argument_words is not None else None),
                                "tracked_argument_object_allocation": (
                                    next(([hex(base), size] for base, size in allocations.items()
                                          if base <= argument_words[1] < base + size), None)
                                    if argument_words is not None else None)})
            if (raw_error is not None or not diagnose_create_game_main
                    or arg0 == 0 or arg0 & 7 or arg1 != 0
                    or argument_words is None):
                unhandled.append({**import_call, "reason": raw_error or
                                  "pthread_create requires observed writable output and default attributes"})
                guest.emu_stop()
                return
            if (caller == BASE + 0x0139E268 and arg2 == BASE + 0x013A1088
                    and len(guest_threads) == 0
                    and extended_argument_words is not None
                    and extended_argument_words[1:] == (BASE + 0x0139E2B0, 0,
                                                        struct.unpack("<Q", guest.mem_read(
                                                            BASE + APPLICATION_SINGLETON_SLOT, 8))[0])):
                thread_handle = WORKER_HANDLE
                thread_id = GUEST_THREAD_ID + 1
                thread_tls = WORKER_TLS
                thread_stack = WORKER_STACK
                thread_kind = "Mainloop"
                callback = argument_words[1]
            elif (caller == BASE + 0x0123B940
                  and arg2 == BASE + 0x0123CA7C
                  and len(guest_threads) == 3
                  and sum(worker["kind"] == "pool" for worker in guest_threads.values()) == 2
                  and WORKER_STACK <= arg0 <= WORKER_STACK + WORKER_STACK_SIZE - 8
                  and allocations.get(start_argument) == 16
                  and allocations.get(argument_words[0]) == 8
                  and import_call["tracked_argument_object_allocation"] is not None):
                thread_handle = POOL_WORKER_HANDLE_BASE + 2 * PAGE
                thread_id = GUEST_THREAD_ID + 4
                thread_tls = POOL_WORKER_TLS_BASE + 2 * PAGE
                thread_stack = POOL_WORKER_STACK_BASE + 2 * WORKER_STACK_SIZE
                thread_kind = "timer"
                callback = BASE + 0x0123C5BC
            elif (caller == BASE + 0x0130FA94
                  and arg2 == BASE + 0x0130FEB0
                  and len(guest_threads) == 4
                  and WORKER_STACK <= arg0 <= WORKER_STACK + WORKER_STACK_SIZE - 8
                  and allocations.get(start_argument) == 40
                  and allocations.get(argument_words[0]) == 8
                  and import_call["tracked_argument_object_allocation"] is not None):
                try:
                    guest.mem_read(start_argument, 40)
                except UcError as error:
                    unhandled.append({**import_call, "reason": str(error)})
                    guest.emu_stop()
                    return
                thread_handle = POOL_WORKER_HANDLE_BASE + 3 * PAGE
                thread_id = GUEST_THREAD_ID + 5
                thread_tls = POOL_WORKER_TLS_BASE + 3 * PAGE
                thread_stack = POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                thread_kind = "graphics_job"
                callback = argument_words[1]
            elif (caller == BASE + 0x012D2DA4
                  and arg2 == BASE + 0x012D65FC
                  and len(guest_threads) == 5
                  and WORKER_STACK <= arg0 <= WORKER_STACK + WORKER_STACK_SIZE - 8
                  and allocations.get(start_argument) == 24
                  and allocations.get(argument_words[0]) == 8
                  and sc3d_argument_words is not None
                  and sc3d_argument_words[1] == BASE + 0x012D3010
                  and sc3d_argument_words[2] != 0):
                try:
                    guest.mem_read(sc3d_argument_words[2], 8)
                except UcError as error:
                    unhandled.append({**import_call, "reason": str(error)})
                    guest.emu_stop()
                    return
                thread_handle = POOL_WORKER_HANDLE_BASE + 4 * PAGE
                thread_id = GUEST_THREAD_ID + 6
                thread_tls = POOL_WORKER_TLS_BASE + 4 * PAGE
                thread_stack = POOL_WORKER_STACK_BASE + 4 * WORKER_STACK_SIZE
                thread_kind = "sc3d_loader"
                callback = sc3d_argument_words[1]
                import_call["owner_object"] = hex(sc3d_argument_words[2])
            elif (caller == BASE + 0x0130FA94
                  and arg2 == BASE + 0x0130FEB0
                  and len(guest_threads) == 6
                  and WORKER_STACK <= arg0 <= WORKER_STACK + WORKER_STACK_SIZE - 8
                  and allocations.get(start_argument) == 40
                  and allocations.get(argument_words[0]) == 8
                  and import_call["tracked_argument_object_allocation"]
                  == [hex(argument_words[1] - 0x10), 472]):
                try:
                    callable_vtable = struct.unpack(
                        "<Q", guest.mem_read(argument_words[1], 8))[0]
                    callable_entry = struct.unpack(
                        "<Q", guest.mem_read(callable_vtable + 8, 8))[0]
                except UcError as error:
                    unhandled.append({**import_call, "reason": str(error)})
                    guest.emu_stop()
                    return
                if not (libg_object["text_start"] <= callable_entry <
                        libg_object["text_start"] + libg_object["text_size"]):
                    unhandled.append({**import_call,
                                      "callable_vtable": hex(callable_vtable),
                                      "callable_entry": hex(callable_entry),
                                      "reason": "observed startup worker callable is outside libg text"})
                    guest.emu_stop()
                    return
                thread_handle = POOL_WORKER_HANDLE_BASE + 5 * PAGE
                thread_id = GUEST_THREAD_ID + 7
                thread_tls = POOL_WORKER_TLS_BASE + 5 * PAGE
                thread_stack = POOL_WORKER_STACK_BASE + 5 * WORKER_STACK_SIZE
                thread_kind = "parallel_queue_worker"
                callback = callable_entry
                import_call.update({"callable_vtable": hex(callable_vtable),
                                    "callable_entry": hex(callable_entry)})
            else:
                pool_count = sum(worker["kind"] == "pool" for worker in guest_threads.values())
                if (caller != BASE + 0x013170D8 or arg2 != BASE + 0x013178D0
                        or pool_count >= 2 or len(guest_threads) != pool_count + 1
                        or extended_argument_words is None
                        or extended_argument_words[0] != BASE + 0x013169BC
                        or extended_argument_words[1] == 0
                        or extended_argument_words[2:] != (0, 0)
                        or arg0 != argument_words[1] + 0x20):
                    unhandled.append({**import_call,
                                      "reason": "only observed Mainloop, pool, timer and graphics jobs are supported"})
                    guest.emu_stop()
                    return
                thread_handle = POOL_WORKER_HANDLE_BASE + pool_count * PAGE
                thread_id = GUEST_THREAD_ID + 2 + pool_count
                thread_tls = POOL_WORKER_TLS_BASE + pool_count * PAGE
                thread_stack = POOL_WORKER_STACK_BASE + pool_count * WORKER_STACK_SIZE
                thread_kind = "pool"
                callback = argument_words[0]
            try:
                guest.mem_write(arg0, struct.pack("<Q", thread_handle))
            except UcError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            guest_threads[thread_handle] = {"start_routine": arg2,
                                            "start_argument": start_argument,
                                            "callback": callback,
                                            "thread_id": thread_id,
                                            "tls": thread_tls,
                                            "stack": thread_stack,
                                            "kind": thread_kind,
                                            "joinable": True, "state": "queued"}
            import_call.update({"guest_thread_handle": hex(thread_handle),
                                "guest_thread_id": thread_id,
                                "worker_kind": thread_kind,
                                "result": 0, "state": "queued"})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_getschedparam":
            worker = guest_threads.get(arg0)
            if (caller != BASE + 0x01316DE4 or worker is None
                    or worker["kind"] != "pool" or arg1 == 0 or arg2 == 0
                    or arg1 & 3 or arg2 & 3):
                unhandled.append({**import_call, "thread_handle": hex(arg0),
                                  "policy_out": hex(arg1), "param_out": hex(arg2),
                                  "reason": "only observed pool scheduler query is supported"})
                guest.emu_stop()
                return
            try:
                guest.mem_write(arg1, struct.pack("<i", 0))  # SCHED_OTHER.
                guest.mem_write(arg2, struct.pack("<i", 0))  # sched_priority.
            except UcError as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"thread_handle": hex(arg0), "policy": 0,
                                "sched_priority": 0, "result": 0})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "pthread_setschedparam":
            worker = guest_threads.get(arg0)
            try:
                priority = struct.unpack("<i", guest.mem_read(arg2, 4))[0] if arg2 else None
            except UcError:
                priority = None
            if (caller != BASE + 0x01316DF8 or worker is None
                    or worker["kind"] != "pool" or arg1 != 3 or priority != 0):
                unhandled.append({**import_call, "thread_handle": hex(arg0),
                                  "policy": arg1, "sched_priority": priority,
                                  "reason": "only observed unprivileged SCHED_BATCH request is supported"})
                guest.emu_stop()
                return
            import_call.update({"thread_handle": hex(arg0), "policy": "SCHED_BATCH",
                                "sched_priority": priority, "result": 1,
                                "reason": "EPERM: controlled guest app cannot set scheduling policy"})
            guest.reg_write(UC_ARM64_REG_X0, 1)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol in ("pthread_cond_wait", "pthread_cond_timedwait"):
            worker_handle = next((handle for handle, worker in guest_threads.items()
                                  if worker["thread_id"] == active_guest_thread_id), None)
            worker = guest_threads.get(worker_handle) if worker_handle is not None else None
            tracked_mutex = mutexes.get(arg1)
            wait_deadline_ns = None
            deadline_pointer = 0
            seconds, nanoseconds = 0, -1
            try:
                raw_condition = bytes(guest.mem_read(arg0, 8))
                if symbol == "pthread_cond_timedwait":
                    deadline_pointer = guest.reg_read(UC_ARM64_REG_X2)
                    seconds, nanoseconds = struct.unpack(
                        "<qq", guest.mem_read(deadline_pointer, 16))
                    wait_deadline_ns = seconds * 1_000_000_000 + nanoseconds
            except UcError as error:
                raw_condition = None
                raw_error = str(error)
            else:
                raw_error = None
            wait_call = {"symbol": symbol, "caller": hex(caller),
                         "condition_pointer": hex(arg0), "mutex_pointer": hex(arg1),
                         "condition_raw": (raw_condition.hex()
                                           if raw_condition is not None else None),
                         "guest_thread_id": active_guest_thread_id,
                         "deadline_wall_ns": wait_deadline_ns}
            condition_adapter_calls.append(wait_call)
            valid_caller = ((symbol == "pthread_cond_wait" and caller == CPP_BASE + 0xDF3B8
                             and worker is not None
                             and worker["kind"] in ("pool", "Mainloop", "graphics_job",
                                                    "sc3d_loader", "parallel_queue_worker"))
                            or (symbol == "pthread_cond_timedwait"
                                and caller == CPP_BASE + 0xDF44C
                                and worker is not None and worker["kind"] in ("Mainloop", "timer")))
            valid_deadline = (symbol == "pthread_cond_wait" or
                              (deadline_pointer != 0 and deadline_pointer & 7 == 0
                               and seconds >= 0 and 0 <= nanoseconds < 1_000_000_000))
            if (raw_error is not None or not valid_caller or not valid_deadline
                    or tracked_mutex is None or tracked_mutex.owner_tid != active_guest_thread_id
                    or tracked_mutex.depth != 1 or raw_condition is None
                    or struct.unpack("<I", raw_condition[:4])[0] not in (0, 2)):
                wait_call.update({"result": "stopped", "reason": raw_error or
                                  "condition wait requires an observed worker, deadline and owned mutex"})
                unhandled.append(wait_call.copy())
                guest.emu_stop()
                return
            if wait_deadline_ns is not None and wait_deadline_ns <= virtual_clock["wall_ns"]:
                wait_call["result"] = "ETIMEDOUT; mutex remained owned"
                guest.reg_write(UC_ARM64_REG_X0, 110)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            tracked_mutex.host_lock.release()
            tracked_mutex.depth = 0
            tracked_mutex.owner_tid = None
            mirror_mutex_state(guest, arg1, tracked_mutex)
            worker["state"] = "waiting_condition"
            worker["wait_condition"] = arg0
            worker["wait_mutex"] = arg1
            worker["wait_return_address"] = guest.reg_read(UC_ARM64_REG_X30)
            worker["wait_deadline_ns"] = wait_deadline_ns
            condition_waiters[arg0] = condition_waiters.get(arg0, 0) + 1
            condition_wait_queue.setdefault(arg0, []).append(worker_handle)
            wait_call["result"] = "parked; mutex atomically released"
            import_call["worker_state"] = "waiting_condition"
            guest.emu_stop()
            return
        if symbol in ("pthread_mutex_lock", "pthread_mutex_unlock",
                      "pthread_mutex_init", "pthread_mutex_destroy"):
            mutex_call = {"symbol": symbol, "mutex_pointer": hex(arg0), "caller": hex(caller)}
            mutex_adapter_calls.append(mutex_call)

            def mutex_failure(reason: str) -> None:
                mutex_call["result"] = "stopped"
                mutex_call["reason"] = reason
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "mutex_pointer": hex(arg0), "reason": reason})
                guest.emu_stop()

            if arg0 == 0 or arg0 & 3:
                mutex_failure("null or unaligned guest mutex pointer")
                return
            try:
                raw_mutex = bytes(guest.mem_read(arg0, 40))
                guest.mem_write(arg0, raw_mutex)  # Require all 40 guest bytes to be writable.
            except UcError:
                mutex_failure("40-byte guest mutex is unmapped or unwritable")
                return
            tracked_mutex = mutexes.get(arg0)
            if tracked_mutex is not None:
                guest_state = struct.unpack_from("<H", raw_mutex)[0]
                if guest_state != mutex_state_bits(tracked_mutex):
                    mutex_failure("guest mutex state differs from tracked host lock state")
                    return
                if (tracked_mutex.recursive and struct.unpack_from("<I", raw_mutex, 4)[0]
                        != (tracked_mutex.owner_tid or 0)):
                    mutex_failure("guest recursive mutex owner differs from tracked host owner")
                    return
            if symbol == "pthread_mutex_init":
                if tracked_mutex is not None:
                    mutex_failure("guest mutex was already initialized")
                    return
                recursive = False
                if arg1 != 0:
                    if arg1 & 7 or arg1 not in initialized_mutex_attrs:
                        mutex_failure("unknown guest mutex attributes")
                        return
                    try:
                        attribute_value = struct.unpack("<Q", bytes(guest.mem_read(arg1, 8)))[0]
                    except UcError:
                        mutex_failure("guest mutex attributes are unmapped")
                        return
                    if attribute_value not in (0, 1):
                        mutex_failure("unsupported guest mutex attribute flags or type")
                        return
                    recursive = attribute_value == 1
                    mutex_call["attr_pointer"] = hex(arg1)
                    mutex_call["attr_value"] = attribute_value
                tracked_mutex = GuestMutex(threading.RLock() if recursive else threading.Lock(), recursive)
                guest.mem_write(arg0, b"\0" * 40)
                mirror_mutex_state(guest, arg0, tracked_mutex)
                mutexes[arg0] = tracked_mutex
            elif symbol == "pthread_mutex_lock":
                if tracked_mutex is None:
                    if raw_mutex != b"\0" * 40:
                        mutex_failure("unknown nonzero guest mutex initializer or destroyed mutex")
                        return
                    tracked_mutex = GuestMutex(threading.Lock(), False)
                    mutexes[arg0] = tracked_mutex
                host_tid = active_guest_thread_id
                if tracked_mutex.recursive and tracked_mutex.depth >= 2048:
                    mutex_failure("guest recursive mutex depth would overflow 11-bit counter")
                    return
                if not tracked_mutex.host_lock.acquire(blocking=False):
                    mutex_failure("single-thread diagnostic would deadlock on this mutex")
                    return
                tracked_mutex.owner_tid = host_tid
                tracked_mutex.depth += 1
                mirror_mutex_state(guest, arg0, tracked_mutex)
            elif symbol == "pthread_mutex_unlock":
                host_tid = active_guest_thread_id
                if (tracked_mutex is None or tracked_mutex.depth == 0
                        or tracked_mutex.owner_tid != host_tid):
                    mutex_failure("unlock of an unknown, unlocked, or foreign-owned guest mutex")
                    return
                tracked_mutex.host_lock.release()
                tracked_mutex.depth -= 1
                if tracked_mutex.depth == 0:
                    tracked_mutex.owner_tid = None
                mirror_mutex_state(guest, arg0, tracked_mutex)
            else:  # pthread_mutex_destroy
                if tracked_mutex is None and raw_mutex != b"\0" * 40:
                    mutex_failure("destroy of an unknown nonzero guest mutex")
                    return
                if tracked_mutex is not None and tracked_mutex.depth != 0:
                    mutex_failure("destroy of a locked guest mutex")
                    return
                mutexes.pop(arg0, None)
                guest.mem_write(arg0, struct.pack("<H", 0xFFFF))
            if tracked_mutex is not None and symbol != "pthread_mutex_destroy":
                mutex_call["recursive"] = tracked_mutex.recursive
                mutex_call["owner_tid"] = tracked_mutex.owner_tid
                mutex_call["depth"] = tracked_mutex.depth
                mutex_call["guest_state"] = hex(mutex_state_bits(tracked_mutex))
            mutex_call["result"] = "ok"
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            return
        if symbol == "getauxval":
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "bsearch":
            comparator = guest.reg_read(UC_ARM64_REG_X4)
            if active_guest_thread_id in active_bsearch:
                unhandled.append({**import_call, "reason": "nested guest bsearch is unsupported"})
                guest.emu_stop()
                return
            if arg2 == 0:
                import_call.update({"count": 0, "element_size": arg3, "result": None})
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            table_bytes = arg2 * arg3
            if (arg0 == 0 or arg1 == 0 or arg2 > 1_000_000 or not 0 < arg3 <= 1 << 20
                    or table_bytes > 64 * 1024 * 1024
                    or not any(begin <= arg1 and arg1 + table_bytes <= end
                               and permissions & UC_PROT_READ
                               for begin, end, permissions in guest.mem_regions())
                    or not any(begin <= comparator <= end and permissions & UC_PROT_EXEC
                               for begin, end, permissions in guest.mem_regions())):
                unhandled.append({**import_call, "key": hex(arg0), "base": hex(arg1),
                                  "count": arg2, "element_size": arg3,
                                  "comparator": hex(comparator),
                                  "reason": "bsearch arguments are outside tracked guest memory bounds"})
                guest.emu_stop()
                return
            import_call.update({"key": hex(arg0), "base": hex(arg1),
                                "count": arg2, "element_size": arg3,
                                "comparator": hex(comparator), "comparisons": []})
            active_bsearch[active_guest_thread_id] = {
                "key": arg0, "base": arg1, "count": arg2, "size": arg3,
                "comparator": comparator, "low": 0, "high": arg2,
                "return_address": guest.reg_read(UC_ARM64_REG_X30),
                "record": import_call,
            }
            start_bsearch_comparison(guest, active_bsearch[active_guest_thread_id])
            return
        elif symbol == "getenv":
            try:
                if arg0 == 0:
                    raise ValueError("null environment-variable name")
                raw_name = bytes(guest.mem_read(arg0, 128))
                if b"\0" not in raw_name:
                    raise ValueError("environment-variable name exceeds 128-byte bound")
                name = raw_name.split(b"\0", 1)[0].decode("ascii")
                if not name or "=" in name:
                    raise ValueError("invalid environment-variable name")
            except (UcError, UnicodeDecodeError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"name": name, "result": None,
                                "basis": "the controlled guest environment has no variables"})
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "__system_property_get":
            import_call["property"] = bytes(guest.mem_read(arg0, 128)).split(b"\0", 1)[0].decode(
                "ascii", errors="replace")
            guest.mem_write(arg1, b"\0")
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "__cxa_atexit":
            # Destructor registration is irrelevant to this short-lived
            # diagnostic, which never unloads the guest image.
            guest.reg_write(UC_ARM64_REG_X0, 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strlen":
            length = 0
            while length < 4096 and bytes(guest.mem_read(arg0 + length, 1)) != b"\0":
                length += 1
            if length == 4096:
                unhandled.append({"symbol": symbol, "caller": hex(caller), "reason": "string length exceeds 4096"})
                guest.emu_stop()
            else:
                guest.reg_write(UC_ARM64_REG_X0, length)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strcmp":
            difference = None
            for index in range(4096):
                left = guest.mem_read(arg0 + index, 1)[0]
                right = guest.mem_read(arg1 + index, 1)[0]
                if left != right or left == 0:
                    difference = left - right
                    break
            if difference is None:
                unhandled.append({"symbol": symbol, "caller": hex(caller),
                                  "reason": "strcmp inputs exceed diagnostic bound"})
                guest.emu_stop()
            else:
                guest.reg_write(UC_ARM64_REG_X0, difference & 0xFFFFFFFFFFFFFFFF)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strcpy":
            try:
                data = bytearray()
                for index in range(4096):
                    value = guest.mem_read(arg1 + index, 1)[0]
                    data.append(value)
                    if value == 0:
                        break
                else:
                    raise ValueError("strcpy source exceeds 4096-byte diagnostic bound")
                if not any(begin <= arg0 and arg0 + len(data) <= end
                           and permissions & UC_PROT_WRITE
                           for begin, end, permissions in guest.mem_regions()):
                    raise ValueError("strcpy destination is not writable guest memory")
                guest.mem_write(arg0, bytes(data))
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"source": hex(arg1), "destination": hex(arg0),
                                "copied_bytes": len(data)})
            guest.reg_write(UC_ARM64_REG_X0, arg0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strcasecmp" and caller == BASE + 0x0138B280:
            try:
                def read_ascii_c_string(pointer: int) -> bytes:
                    if pointer == 0:
                        raise ValueError("null strcasecmp input")
                    value = bytearray()
                    for index in range(4096):
                        char = guest.mem_read(pointer + index, 1)[0]
                        if char == 0:
                            return bytes(value)
                        if char > 0x7F:
                            raise ValueError("strcasecmp input is not ASCII")
                        value.append(char)
                    raise ValueError("strcasecmp input exceeds diagnostic bound")
                left = read_ascii_c_string(arg0).lower()
                right = read_ascii_c_string(arg1).lower()
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            difference = next((a - b for a, b in zip(left + b"\0", right + b"\0")
                               if a != b), 0)
            import_call.update({"left": left.decode("ascii"),
                                "right": right.decode("ascii"),
                                "result": difference})
            guest.reg_write(UC_ARM64_REG_X0, difference & 0xFFFFFFFFFFFFFFFF)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol in ("memcpy", "memmove") and arg2 <= 16 * 1024 * 1024:
            guest.mem_write(arg0, bytes(guest.mem_read(arg1, arg2)))
            guest.reg_write(UC_ARM64_REG_X0, arg0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "memset" and arg2 <= 16 * 1024 * 1024:
            guest.mem_write(arg0, bytes([arg1 & 0xFF]) * arg2)
            guest.reg_write(UC_ARM64_REG_X0, arg0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "memcmp" and arg2 <= 16 * 1024 * 1024:
            left = bytes(guest.mem_read(arg0, arg2))
            right = bytes(guest.mem_read(arg1, arg2))
            difference = next((a - b for a, b in zip(left, right) if a != b), 0)
            guest.reg_write(UC_ARM64_REG_X0, difference & 0xFFFFFFFFFFFFFFFF)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "memchr" and arg2 <= 16 * 1024 * 1024:
            haystack = bytes(guest.mem_read(arg0, arg2)) if arg2 else b""
            position = haystack.find(bytes([arg1 & 0xFF]))
            guest.reg_write(UC_ARM64_REG_X0, arg0 + position if position >= 0 else 0)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strrchr":
            try:
                if arg0 == 0:
                    raise ValueError("null strrchr input")
                needle = arg1 & 0xFF
                last_index = -1
                for index in range(4096):
                    value = guest.mem_read(arg0 + index, 1)[0]
                    if value == needle:
                        last_index = index
                    if value == 0:
                        break
                else:
                    raise ValueError("strrchr input exceeds 4096-byte diagnostic bound")
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            result = arg0 + last_index if last_index >= 0 else 0
            import_call.update({"character": needle, "result": hex(result)})
            guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "strchr" and caller == BASE + 0x0139D2B4 and arg0:
            try:
                needle = arg1 & 0xFF
                result = None
                for index in range(4096):
                    value = guest.mem_read(arg0 + index, 1)[0]
                    if value == needle:
                        result = arg0 + index
                        break
                    if value == 0:
                        result = 0
                        break
                if result is None:
                    raise ValueError("strchr input exceeds 4096-byte diagnostic bound")
            except (UcError, ValueError) as error:
                unhandled.append({**import_call, "reason": str(error)})
                guest.emu_stop()
                return
            import_call.update({"character": needle, "result": hex(result)})
            guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol in ("malloc", "_Znwm", "_Znam"):
            guest.reg_write(UC_ARM64_REG_X0, allocate(arg0))
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "calloc":
            guest.reg_write(UC_ARM64_REG_X0, allocate(arg0 * arg1))
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "realloc":
            if arg0 == 0:
                replacement = allocate(arg1)
            else:
                replacement = allocate(arg1)
                if replacement:
                    guest.mem_write(replacement, bytes(guest.mem_read(arg0, min(
                        allocations.get(arg0, 0), arg1))))
            guest.reg_write(UC_ARM64_REG_X0, replacement)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol in ("free", "_ZdlPv", "_ZdaPv"):
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
        elif symbol == "pclose":
            expected_pipe_close = {
                "/system/bin/which su": LIBG_WHICH_SU_PCLOSE_CALL,
                "/system/xbin/which su": LIBG_XBIN_WHICH_SU_PCLOSE_CALL,
            }.get(virtual_which_su_pipe["command"])
            if (diagnose_create_game_main and caller == expected_pipe_close
                    and arg0 == VIRTUAL_WHICH_SU_PIPE
                    and virtual_which_su_pipe["state"] == "eof"):
                virtual_which_su_pipe["state"] = "closed"
                close_call = {"symbol": symbol, "caller": hex(caller),
                              "file_handle": hex(arg0), "result": 127 << 8,
                              "reason": "guest shell command path is absent"}
                virtual_stdio_calls.append(close_call)
                import_call.update(close_call)
                guest.reg_write(UC_ARM64_REG_X0, 127 << 8)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "file_handle": hex(arg0),
                              "reason": "only the observed virtual which-su pipe is supported"})
            guest.emu_stop()
        elif symbol == "popen":
            try:
                raw_command = bytearray()
                for index in range(512):
                    item = guest.mem_read(arg0 + index, 1)[0]
                    if item == 0:
                        break
                    raw_command.append(item)
                else:
                    raise ValueError("guest popen command exceeds 512 bytes")
                command_text = raw_command.decode("utf-8")
                raw_mode = bytearray()
                for index in range(16):
                    item = guest.mem_read(arg1 + index, 1)[0]
                    if item == 0:
                        break
                    raw_mode.append(item)
                else:
                    raise ValueError("guest popen mode exceeds 16 bytes")
                mode_text = raw_mode.decode("ascii")
                reason = None
            except (UcError, UnicodeDecodeError, ValueError) as error:
                command_text = None
                mode_text = None
                reason = str(error)
            expected_pipe_open = {
                "/system/bin/which su": LIBG_WHICH_SU_POPEN_CALL,
                "/system/xbin/which su": LIBG_XBIN_WHICH_SU_POPEN_CALL,
            }.get(command_text)
            if (reason is None and diagnose_create_game_main and assets_dir_metadata is not None
                    and caller == expected_pipe_open and mode_text == "r"
                    and virtual_which_su_pipe["state"] in ("unopened", "closed")
                    and (virtual_which_su_pipe["command"] is None
                         or virtual_which_su_pipe["command"] == "/system/bin/which su")):
                virtual_which_su_pipe["state"] = "open"
                virtual_which_su_pipe["command"] = command_text
                open_call = {"symbol": symbol, "caller": hex(caller),
                             "command": command_text, "mode": mode_text,
                             "result": hex(VIRTUAL_WHICH_SU_PIPE),
                             "execution": "virtual empty output; no host process started"}
                virtual_stdio_calls.append(open_call)
                import_call.update(open_call)
                guest.reg_write(UC_ARM64_REG_X0, VIRTUAL_WHICH_SU_PIPE)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "command": command_text, "mode": mode_text,
                              "reason": reason or "guest popen command is not implemented"})
            guest.emu_stop()
        elif symbol == "powf":
            inputs = [struct.unpack("<f", struct.pack(
                "<I", guest.reg_read(register) & 0xFFFFFFFF))[0]
                for register in (UC_ARM64_REG_S0, UC_ARM64_REG_S1)]
            function = host_math_functions.get("powf")
            if function is None:
                function = ctypes.CDLL("ucrtbase.dll").powf
                function.argtypes = [ctypes.c_float, ctypes.c_float]
                function.restype = ctypes.c_float
                host_math_functions["powf"] = function
            result = function(*inputs)
            result_bits = struct.unpack("<I", struct.pack("<f", result))[0]
            guest.reg_write(UC_ARM64_REG_S0, result_bits)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
            import_call.update({"inputs": inputs, "result": result,
                                "basis": "Windows UCRT single-precision powf"})
            return
        elif symbol == "opendir":
            try:
                raw_path = bytearray()
                for index in range(512):
                    item = guest.mem_read(arg0 + index, 1)[0]
                    if item == 0:
                        break
                    raw_path.append(item)
                else:
                    raise ValueError("guest opendir path exceeds 512 bytes")
                path_text = raw_path.decode("utf-8")
                reason = None
            except (UcError, UnicodeDecodeError, ValueError) as error:
                path_text = None
                reason = str(error)
            expected_cache_path = "/data/user/0/" + pinned_package + "/cache"
            if (reason is None and diagnose_create_game_main
                    and assets_dir_metadata is not None
                    and caller == BASE + 0x011959D4
                    and path_text == expected_cache_path
                    and virtual_cache_dir["state"] in ("unopened", "closed")
                    and cache_host_dir.is_dir()):
                virtual_cache_dir["state"] = "open"
                import_call.update({"path": path_text, "result": hex(0x69000000),
                                    "host_directory": str(cache_host_dir)})
                guest.reg_write(UC_ARM64_REG_X0, 0x69000000)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "path": path_text,
                              "reason": reason or "virtual guest opendir is not implemented"})
            guest.emu_stop()
        elif symbol == "readdir":
            if (diagnose_create_game_main and assets_dir_metadata is not None
                    and caller in (BASE + 0x011959E0, BASE + 0x01195A1C)
                    and arg0 == 0x69000000
                    and virtual_cache_dir["state"] in ("open", "eof")
                    and not any(cache_host_dir.iterdir())):
                virtual_cache_dir["state"] = "eof"
                import_call.update({"directory": hex(arg0), "result": None,
                                    "reason": "controlled guest cache directory is empty"})
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({**import_call, "directory": hex(arg0),
                              "reason": "virtual guest readdir requires an empty tracked cache directory"})
            guest.emu_stop()
        elif symbol == "closedir":
            if (diagnose_create_game_main and assets_dir_metadata is not None
                    and caller == BASE + 0x01195A2C and arg0 == 0x69000000
                    and virtual_cache_dir["state"] in ("open", "eof")):
                virtual_cache_dir["state"] = "closed"
                import_call.update({"directory": hex(arg0), "result": 0})
                guest.reg_write(UC_ARM64_REG_X0, 0)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            unhandled.append({**import_call, "directory": hex(arg0),
                              "reason": "virtual guest closedir requires the tracked cache directory"})
            guest.emu_stop()
        elif symbol == "stat":
            try:
                raw_path = bytearray()
                for index in range(512):
                    item = guest.mem_read(arg0 + index, 1)[0]
                    if item == 0:
                        break
                    raw_path.append(item)
                else:
                    raise ValueError("guest stat path exceeds 512 bytes")
                path_text = raw_path.decode("utf-8")
                reason = None
            except (UcError, UnicodeDecodeError, ValueError) as error:
                path_text = None
                reason = str(error)
            known_absent_root_paths = {
                "/system/app/Superuser.apk",
                "/system/app/SuperSU/SuperSU.apk",
                "/sbin/magisk",
                "/sbin/.magisk",
            }
            if (reason is None and diagnose_create_game_main and assets_dir_metadata is not None
                    and path_text in known_absent_root_paths and arg1 != 0):
                import_call.update({"guest_path": path_text, "result": -1,
                                    "reason": "path absent from controlled guest system image",
                                    "errno_note": "ENOENT implied by missing path; guest errno not yet exposed"})
                guest.reg_write(UC_ARM64_REG_X0, 0xffffffffffffffff)
                guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                return
            update_asset_prefix = "/data/user/0/" + pinned_package + "/update/"
            if (reason is None and diagnose_create_game_main and assets_dir_metadata is not None
                    and path_text is not None and path_text.startswith(update_asset_prefix)
                    and arg1 != 0):
                asset_name = path_text[len(update_asset_prefix):]
                parts = asset_name.split("/")
                if (asset_name and "\\" not in asset_name and ":" not in asset_name
                        and all(part not in ("", ".", "..") for part in parts)):
                    guest_path = (guest_fs_root / "data" / "user" / "0" / pinned_package
                                  / "update" / Path(*parts)).resolve()
                    if (guest_path.is_relative_to(guest_fs_root)
                            and not guest_path.exists()):
                        import_call.update({"guest_path": path_text, "result": -1,
                                            "reason": "update file absent from controlled guest filesystem",
                                            "errno_note": "ENOENT implied by missing path; guest errno not yet exposed"})
                        guest.reg_write(UC_ARM64_REG_X0, 0xffffffffffffffff)
                        guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                        return
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "path": path_text, "stat_buffer": hex(arg1),
                              "reason": reason or "virtual guest stat is not implemented"})
            guest.emu_stop()
        elif symbol == "mkdir":
            try:
                raw_path = bytearray()
                for index in range(512):
                    item = guest.mem_read(arg0 + index, 1)[0]
                    if item == 0:
                        break
                    raw_path.append(item)
                else:
                    raise ValueError("guest mkdir path exceeds 512 bytes")
                path_text = raw_path.decode("utf-8")
            except (UcError, UnicodeDecodeError, ValueError) as error:
                path_text = None
                reason = str(error)
            else:
                reason = None
            if (reason is None and diagnose_create_game_main and assets_dir_metadata is not None
                    and arg1 == 0o770 and path_text is not None):
                parts = path_text.split("/")
                expected_prefixes = {"/data", "/data/user", "/data/user/0"}
                package_root = "/data/user/0/" + pinned_package
                valid_path = (path_text in expected_prefixes or path_text == package_root
                              or path_text.startswith(package_root + "/"))
                if (path_text.startswith("/") and valid_path and all(
                        part and part not in (".", "..") and "\\" not in part and ":" not in part
                        for part in parts[1:])):
                    host_path = (guest_fs_root / Path(*parts[1:])).resolve()
                    if host_path.is_relative_to(guest_fs_root):
                        try:
                            existed = host_path.is_dir()
                            host_path.mkdir(parents=True, exist_ok=True)
                        except OSError as error:
                            reason = str(error)
                        else:
                            import_call.update({"guest_path": path_text,
                                                "host_root": str(guest_fs_root),
                                                "host_relative": host_path.relative_to(guest_fs_root).as_posix(),
                                                "mode": oct(arg1), "already_existed": existed,
                                                "result": 0})
                            guest.reg_write(UC_ARM64_REG_X0, 0)
                            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))
                            return
                else:
                    reason = "mkdir path is outside the tracked virtual data namespace"
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "path": path_text, "mode": oct(arg1),
                              "reason": reason or "virtual guest directory creation is not implemented"})
            guest.emu_stop()
        else:
            unhandled.append({"symbol": symbol, "caller": hex(caller),
                              "arguments_x0_x3": [hex(arg0), hex(arg1), hex(arg2),
                                                  hex(guest.reg_read(UC_ARM64_REG_X3))]})
            guest.emu_stop()

    def on_invalid(_machine: Uc, access: int, address: int, size: int, _value: int, _user_data: object) -> bool:
        faults.append({"access": access, "address": hex(address), "size": size,
                       "program_counter": hex(_machine.reg_read(UC_ARM64_REG_PC))})
        return False

    def on_real_cpp_mutex(_machine: Uc, _address: int, _size: int, _user_data: object) -> None:
        nonlocal real_cpp_mutex_entries
        real_cpp_mutex_entries += 1

    def stop_dynamic_linker(guest: Uc, reason: str, address: int) -> None:
        unhandled.append({"symbol": "dl_iterate_phdr", "caller": hex(address), "reason": reason})
        guest.emu_stop()

    def on_dl_iterate(guest: Uc, address: int, _size: int, _user_data: object) -> None:
        nonlocal active_iteration
        if address == DL_ITERATE_PHDR:
            if active_iteration is not None:
                stop_dynamic_linker(guest, "recursive dl_iterate_phdr call is unsupported", address)
                return
            callback = guest.reg_read(UC_ARM64_REG_X0)
            if callback != LIBG_PHDR_CALLBACK:
                stop_dynamic_linker(guest, "unknown guest program-header callback", address)
                return
            active_iteration = {
                "callback": callback,
                "data": guest.reg_read(UC_ARM64_REG_X1),
                "return_address": guest.reg_read(UC_ARM64_REG_X30),
                "index": 0,
                "mprotect_stage": 0,
                "patches": [],
                "visited": [],
            }
            phdr_iteration_calls.append(active_iteration)
        elif address == DL_ITERATE_CONTINUE:
            if active_iteration is None:
                stop_dynamic_linker(guest, "callback returned without active iteration", address)
                return
            callback_result = guest.reg_read(UC_ARM64_REG_X0)
            current_name = phdr_infos[active_iteration["index"]]["name"]
            active_iteration["visited"][-1]["callback_result"] = hex(callback_result)
            if current_name == "libg.so":
                if (callback_result == 0 or active_iteration["mprotect_stage"] != 2
                        or not active_iteration["patches"]):
                    stop_dynamic_linker(guest, "libg callback did not finish both protections and text patches", address)
                    return
                for patch in active_iteration["patches"]:
                    if bytes(guest.mem_read(int(patch["address"], 16), 4)) != struct.pack("<I", 0xD65F03C0):
                        stop_dynamic_linker(guest, "patched guest instruction does not contain RET", address)
                        return
                guest.reg_write(UC_ARM64_REG_X30, active_iteration["return_address"])
                guest.reg_write(UC_ARM64_REG_PC, active_iteration["return_address"])
                active_iteration = None
                return
            if callback_result != 0:
                stop_dynamic_linker(guest, "non-libg callback unexpectedly stopped iteration", address)
                return
            active_iteration["index"] += 1
        else:
            stop_dynamic_linker(guest, "unknown dynamic-linker trampoline entry", address)
            return

        if active_iteration["index"] >= len(phdr_infos):
            stop_dynamic_linker(guest, "libg callback was not visited", address)
            return
        info = phdr_infos[active_iteration["index"]]
        active_iteration["visited"].append({"name": info["guest_name"],
                                             "info_address": hex(info["info_address"]),
                                             "phdr_address": hex(info["phdr_address"]),
                                             "phnum": info["phnum"]})
        guest.reg_write(UC_ARM64_REG_X0, info["info_address"])
        guest.reg_write(UC_ARM64_REG_X1, 64)
        guest.reg_write(UC_ARM64_REG_X2, active_iteration["data"])
        guest.reg_write(UC_ARM64_REG_X30, DL_ITERATE_CONTINUE)
        guest.reg_write(UC_ARM64_REG_PC, active_iteration["callback"])

    def start_bsearch_comparison(guest: Uc, state: dict) -> None:
        midpoint = (state["low"] + state["high"]) // 2
        state["midpoint"] = midpoint
        guest.reg_write(UC_ARM64_REG_X0, state["key"])
        guest.reg_write(UC_ARM64_REG_X1, state["base"] + midpoint * state["size"])
        guest.reg_write(UC_ARM64_REG_X30, BSEARCH_CONTINUE)
        guest.reg_write(UC_ARM64_REG_PC, state["comparator"])

    def on_bsearch_continue(guest: Uc, address: int, _size: int,
                            _user_data: object) -> None:
        state = active_bsearch.get(active_guest_thread_id)
        if address != BSEARCH_CONTINUE or state is None:
            unhandled.append({"symbol": "bsearch", "caller": hex(address),
                              "reason": "comparison returned without matching guest bsearch"})
            guest.emu_stop()
            return
        raw_result = guest.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF
        comparison = raw_result if raw_result < 0x80000000 else raw_result - 0x100000000
        midpoint = state["midpoint"]
        state["record"]["comparisons"].append({"index": midpoint,
                                                  "result": comparison})
        if comparison == 0:
            result = state["base"] + midpoint * state["size"]
        else:
            if comparison < 0:
                state["high"] = midpoint
            else:
                state["low"] = midpoint + 1
            if state["low"] < state["high"]:
                start_bsearch_comparison(guest, state)
                return
            result = 0
        return_address = state["return_address"]
        state["record"]["result"] = hex(result)
        del active_bsearch[active_guest_thread_id]
        guest.reg_write(UC_ARM64_REG_X0, result)
        guest.reg_write(UC_ARM64_REG_X30, return_address)
        guest.reg_write(UC_ARM64_REG_PC, return_address)

    def on_once_return(guest: Uc, address: int, _size: int, _user_data: object) -> None:
        if (address != PTHREAD_ONCE_RETURN or not once_pending
                or once_pending["guest_thread_id"] != active_guest_thread_id):
            unhandled.append({"symbol": "pthread_once", "caller": hex(address),
                              "reason": "once initializer returned without matching guest thread"})
            guest.emu_stop()
            return
        control = once_pending["control"]
        if bytes(guest.mem_read(control, 4)) != struct.pack("<I", 1):
            unhandled.append({"symbol": "pthread_once", "caller": hex(address),
                              "reason": "once initializer changed its control state"})
            guest.emu_stop()
            return
        guest.mem_write(control, struct.pack("<I", 2))
        completed_once_controls.add(control)
        return_address = once_pending["return_address"]
        once_pending.clear()
        guest.reg_write(UC_ARM64_REG_X0, 0)
        guest.reg_write(UC_ARM64_REG_X30, return_address)
        guest.reg_write(UC_ARM64_REG_PC, return_address)

    def on_mprotect(guest: Uc, address: int, _size: int, _user_data: object) -> None:
        if active_iteration is None or phdr_infos[active_iteration["index"]]["name"] != "libg.so":
            stop_dynamic_linker(guest, "mprotect SVC outside libg callback", address)
            return
        stage = active_iteration["mprotect_stage"]
        expected_address = LIBG_MPROTECT_WRITABLE if stage == 0 else LIBG_MPROTECT_RESTORE
        expected_protection = 7 if stage == 0 else 5
        start = guest.reg_read(UC_ARM64_REG_X0)
        length = guest.reg_read(UC_ARM64_REG_X1)
        protection = guest.reg_read(UC_ARM64_REG_X2)
        syscall_number = guest.reg_read(UC_ARM64_REG_X8)
        if (stage > 1 or address != expected_address or syscall_number != 226
                or start != libg_object["text_start"] or length != libg_object["text_size"]
                or protection != expected_protection or start & (PAGE - 1)):
            stop_dynamic_linker(guest, "unexpected mprotect SVC arguments or order", address)
            return
        try:
            guest.mem_protect(start, round_up(length),
                              UC_PROT_READ | UC_PROT_EXEC | (UC_PROT_WRITE if stage == 0 else 0))
        except UcError:
            stop_dynamic_linker(guest, "guest text protection change failed", address)
            return
        mprotect_calls.append({"site": hex(address), "address": hex(start),
                               "length": length, "protection": protection, "result": 0})
        active_iteration["mprotect_stage"] = stage + 1
        guest.reg_write(UC_ARM64_REG_X0, 0)
        guest.reg_write(UC_ARM64_REG_PC, address + 4)

    def on_text_write(guest: Uc, _access: int, address: int, size: int,
                      value: int, _user_data: object) -> None:
        if (active_iteration is None or active_iteration["mprotect_stage"] != 1
                or phdr_infos[active_iteration["index"]]["name"] != "libg.so"
                or size != 4 or value != 0xD65F03C0 or address & 3):
            stop_dynamic_linker(guest, "unexpected guest write to libg executable segment", address)
            return
        patch = {"address": hex(address), "previous_instruction":
                 bytes(guest.mem_read(address, 4)).hex(), "instruction": "d65f03c0"}
        active_iteration["patches"].append(patch)
        patched_instructions.append(patch)

    invalid_handle = machine.hook_add(UC_HOOK_MEM_INVALID, on_invalid)
    import_handle = machine.hook_add(UC_HOOK_CODE, on_import,
                                     begin=STUB, end=STUB + round_up(len(symbol_for_stub) * 16) - 1)
    gl_import_handle = machine.hook_add(UC_HOOK_CODE, on_import,
                                        begin=GL_STUB_BASE, end=GL_STUB_BASE + GL_STUB_SIZE - 1)
    def on_graphics_checkpoint(guest: Uc, address: int, _size: int,
                               _user_data: object) -> None:
        checkpoint = {"address": hex(address),
                      "x0": guest.reg_read(UC_ARM64_REG_X0),
                      "guest_thread_id": active_guest_thread_id}
        if address == BASE + 0x0161860C:
            checkpoint["parsed_stack_words"] = list(struct.unpack(
                "<II", guest.mem_read(guest.reg_read(UC_ARM64_REG_SP), 8)))
        if address == BASE + 0x0161A22C:
            version_ptr = struct.unpack("<Q", guest.mem_read(
                BASE + 0x018E8BC0, 8))[0]
            checkpoint["version_ptr"] = hex(version_ptr)
            checkpoint["version_words"] = list(struct.unpack(
                "<II", guest.mem_read(version_ptr, 8)))
        graphics_checkpoints.append(checkpoint)
    graphics_checkpoint_handles = [machine.hook_add(
        UC_HOOK_CODE, on_graphics_checkpoint,
        begin=BASE + offset, end=BASE + offset)
        for offset in (0x0161860C, 0x0161A22C, 0x013DAAAC, 0x00756BE8)]
    cpp_handle = (machine.hook_add(UC_HOOK_CODE, on_real_cpp_mutex,
                                   begin=real_cpp_mutex_address, end=real_cpp_mutex_address)
                  if real_cpp_mutex_address is not None else None)
    dl_handle = machine.hook_add(UC_HOOK_CODE, on_dl_iterate,
                                 begin=DL_ITERATE_PHDR, end=DL_ITERATE_CONTINUE)
    once_handle = machine.hook_add(UC_HOOK_CODE, on_once_return,
                                   begin=PTHREAD_ONCE_RETURN, end=PTHREAD_ONCE_RETURN)
    bsearch_handle = machine.hook_add(UC_HOOK_CODE, on_bsearch_continue,
                                      begin=BSEARCH_CONTINUE, end=BSEARCH_CONTINUE)
    mprotect_first_handle = machine.hook_add(UC_HOOK_CODE, on_mprotect,
                                             begin=LIBG_MPROTECT_WRITABLE, end=LIBG_MPROTECT_WRITABLE)
    mprotect_second_handle = machine.hook_add(UC_HOOK_CODE, on_mprotect,
                                              begin=LIBG_MPROTECT_RESTORE, end=LIBG_MPROTECT_RESTORE)
    text_write_handle = machine.hook_add(UC_HOOK_MEM_WRITE, on_text_write,
                                         begin=libg_object["text_start"],
                                         end=libg_object["text_start"] + libg_object["text_size"] - 1)
    def attempt_prefix(addresses: list[int], limit: int) -> dict:
        completed = 0
        first_import = len(imports_seen)
        first_fault = len(faults)
        first_unhandled = len(unhandled)
        first_mutex_call = len(mutex_adapter_calls)
        first_syscall_call = len(syscall_adapter_calls)
        first_condition_call = len(condition_adapter_calls)
        first_locale_call = len(locale_adapter_calls)
        first_telemetry_call = len(telemetry_adapter_calls)
        first_phdr_call = len(phdr_iteration_calls)
        first_mprotect_call = len(mprotect_calls)
        first_patch = len(patched_instructions)
        first_cpp_mutex_entry = real_cpp_mutex_entries
        for index, address in enumerate(addresses[:limit]):
            try:
                call(machine, address - BASE, instruction_budget=1_000_000,
                     timeout_microseconds=30_000_000)
                completed += 1
            except (UcError, RuntimeError) as error:
                return {
                    "diagnostic_stubs": diagnostic_stubs,
                    "returned_prefix_count": completed,
                    "first_failure_index": index, "first_failure_address": hex(address),
                    "error": str(error), "program_counter": hex(machine.reg_read(UC_ARM64_REG_PC)),
                    "memory_faults": faults[first_fault:], "imports_seen": imports_seen[first_import:],
                    "mutex_adapter_calls": mutex_adapter_calls[first_mutex_call:],
                    "syscall_adapter_calls": syscall_adapter_calls[first_syscall_call:],
                    "condition_adapter_calls": condition_adapter_calls[first_condition_call:],
                    "locale_adapter_calls": locale_adapter_calls[first_locale_call:],
                    "telemetry_adapter_calls": telemetry_adapter_calls[first_telemetry_call:],
                    "phdr_iteration_calls": phdr_iteration_calls[first_phdr_call:],
                    "mprotect_calls": mprotect_calls[first_mprotect_call:],
                    "patched_instructions": patched_instructions[first_patch:],
                    "real_cpp_mutex_function_entries": real_cpp_mutex_entries - first_cpp_mutex_entry,
                    "first_unhandled_import": (unhandled[first_unhandled]
                                               if len(unhandled) > first_unhandled else None),
                }
        return {"diagnostic_stubs": diagnostic_stubs,
                "returned_prefix_count": completed,
                "examined_limit": min(limit, len(addresses)), "first_failure_index": None,
                "imports_seen": imports_seen[first_import:],
                "mutex_adapter_calls": mutex_adapter_calls[first_mutex_call:],
                "syscall_adapter_calls": syscall_adapter_calls[first_syscall_call:],
                "condition_adapter_calls": condition_adapter_calls[first_condition_call:],
                "locale_adapter_calls": locale_adapter_calls[first_locale_call:],
                "telemetry_adapter_calls": telemetry_adapter_calls[first_telemetry_call:],
                "phdr_iteration_calls": phdr_iteration_calls[first_phdr_call:],
                "mprotect_calls": mprotect_calls[first_mprotect_call:],
                "patched_instructions": patched_instructions[first_patch:],
                "real_cpp_mutex_function_entries": real_cpp_mutex_entries - first_cpp_mutex_entry}

    def attempt_jni_onload() -> dict:
        nonlocal active_guest_thread_id
        # This is a registration probe. Java classes are opaque guest handles;
        # no Java methods, app lifecycle, or battle state are constructed.
        machine.mem_map(JNI_BRIDGE, 4 * PAGE)
        machine.mem_map(JNI_TLS, PAGE)
        machine.mem_write(JNI_TLS + 0x28, struct.pack("<Q", JNI_TLS_CANARY))
        machine.reg_write(UC_ARM64_REG_TPIDR_EL0, JNI_TLS)
        machine.mem_write(JNI_VM, struct.pack("<Q", JNI_VM_TABLE))
        for thread_index in range(8):
            machine.mem_write(JNI_ENV + thread_index * 8,
                              struct.pack("<Q", JNI_ENV_TABLE))
        for index in range(8):
            machine.mem_write(JNI_VM_TABLE + index * 8,
                              struct.pack("<Q", JNI_VM_STUBS + index * 16))
        for index in range(JNI_TABLE_SLOTS):
            machine.mem_write(JNI_ENV_TABLE + index * 8,
                              struct.pack("<Q", JNI_ENV_STUBS + index * 16))
        machine.mem_write(JNI_ENV_STUBS, struct.pack("<I", 0xD65F03C0) * JNI_TABLE_SLOTS * 4)
        machine.mem_write(JNI_VM_STUBS, struct.pack("<I", 0xD65F03C0) * 8 * 4)
        classes: list[dict] = []
        class_by_name: dict[str, dict] = {}
        class_by_handle: dict[int, dict] = {}
        tracked_global_ref_handles: dict[int, str] = {}
        tracked_guest_strings: dict[int, dict[str, str]] = {}
        tracked_guest_arrays: dict[int, list[str]] = {}
        active_string_chars: dict[int, dict] = {}
        active_string_utf_chars: dict[int, dict] = {}
        if diagnose_create_game_main and assets_dir_metadata is not None:
            data_dir = "/data/user/0/" + pinned_package
            tracked_guest_strings = {
                JNI_OPAQUE_DATA_DIR: {"label": "dataDir_String", "value": data_dir},
                JNI_OPAQUE_CACHE_DIR: {"label": "cacheDir_String",
                                       "value": data_dir + "/cache"},
                JNI_OPAQUE_UNKNOWN: {"label": "unknown_String", "value": "(unknown)"},
            }
        issued_static_method_ids: set[int] = set()
        issued_app_version_method_ids: set[int] = set()
        issued_app_keyvalue_method_ids: set[int] = set()
        issued_app_store_keyvalue_method_ids: set[int] = set()
        guest_preferences: dict[str, str] = {}
        issued_context_method_ids: set[int] = set()
        issued_context_assets_method_ids: set[int] = set()
        issued_asset_manager_list_method_ids: set[int] = set()
        issued_pm_method_ids: set[int] = set()
        issued_activity_intent_method_ids: set[int] = set()
        issued_intent_has_extra_method_ids: set[int] = set()
        issued_intent_get_flags_method_ids: set[int] = set()
        issued_jni_local_refs: dict[int, int] = {}
        jni_local_frames: dict[int, list[dict[int, int]]] = {}
        emulator_bytecode_source: str | None = None
        if diagnose_create_game_main:
            smali_path = (ROOT / ".local-runtime/build-20260923-224545/decoded/smali"
                          / "com/supercell/titan/GameApp.smali")
            smali_lines = smali_path.read_text(encoding="utf-8").splitlines()
            method_header = ".method public static isEmulator()Z"
            starts = [index for index, line in enumerate(smali_lines)
                      if line.strip() == method_header]
            if len(starts) != 1 or any(line.strip().startswith(".method") and "<clinit>" in line
                                       for line in smali_lines):
                raise ValueError("GameApp APK bytecode has an unexpected isEmulator method or class initializer")
            end = next((index for index in range(starts[0] + 1, len(smali_lines))
                        if smali_lines[index].strip() == ".end method"), None)
            if end is None:
                raise ValueError("GameApp isEmulator APK method has no end")
            opcodes = [line.strip() for line in smali_lines[starts[0] + 1:end]
                       if line.strip() and not line.strip().startswith((".", ":", "#"))]
            if opcodes != ["const/4 v0, 0x0", "return v0"]:
                raise ValueError("GameApp isEmulator APK bytecode is not the verified false return")
            emulator_bytecode_source = (
                str(smali_path.relative_to(ROOT)).replace("\\", "/")
                + f":{starts[0] + 1}-{end + 1}")
        jni_calls: list[dict] = []
        attached_guest_threads: set[int] = set()
        first_import = len(imports_seen)
        first_fault = len(faults)
        first_unhandled = len(unhandled)

        def read_guest_string(guest: Uc, pointer: int, maximum: int) -> str:
            if pointer == 0:
                raise ValueError("null JNI string pointer")
            chars = bytearray()
            for index in range(maximum):
                byte = guest.mem_read(pointer + index, 1)[0]
                if byte == 0:
                    if not chars:
                        raise ValueError("empty JNI string")
                    return chars.decode("ascii")
                chars.append(byte)
            raise ValueError("unterminated JNI string")

        def stop_jni(guest: Uc, api: str, caller: int, reason: str,
                     details: dict | None = None) -> None:
            failure = {"symbol": api, "caller": hex(caller), "reason": reason,
                       "arguments_x0_x3": [hex(guest.reg_read(register)) for register in
                                           (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                            UC_ARM64_REG_X2, UC_ARM64_REG_X3)]}
            if details:
                failure.update(details)
            unhandled.append(failure)
            guest.emu_stop()

        def on_jni_api(guest: Uc, address: int, _size: int, _user_data: object) -> None:
            is_vm = JNI_VM_STUBS <= address < JNI_VM_STUBS + 8 * 16
            index = (address - (JNI_VM_STUBS if is_vm else JNI_ENV_STUBS)) // 16
            api = ("JavaVM.AttachCurrentThread" if is_vm and index == 4 else
                   {6: "JNIEnv.FindClass", 16: "JNIEnv.ExceptionDescribe",
                    17: "JNIEnv.ExceptionClear", 215: "JNIEnv.RegisterNatives",
                    21: "JNIEnv.NewGlobalRef", 22: "JNIEnv.DeleteGlobalRef",
                    19: "JNIEnv.PushLocalFrame", 20: "JNIEnv.PopLocalFrame",
                    23: "JNIEnv.DeleteLocalRef",
                    25: "JNIEnv.NewLocalRef",
                    33: "JNIEnv.GetMethodID",
                    35: "JNIEnv.CallObjectMethodV",
                    38: "JNIEnv.CallBooleanMethodV",
                    50: "JNIEnv.CallIntMethodV",
                    56: "JNIEnv.CallFloatMethodV",
                    113: "JNIEnv.GetStaticMethodID", 115: "JNIEnv.CallStaticObjectMethodV",
                    142: "JNIEnv.CallStaticVoidMethodV",
                    118: "JNIEnv.CallStaticBooleanMethodV",
                    164: "JNIEnv.GetStringLength", 165: "JNIEnv.GetStringChars",
                    166: "JNIEnv.ReleaseStringChars", 167: "JNIEnv.NewStringUTF",
                    169: "JNIEnv.GetStringUTFChars", 170: "JNIEnv.ReleaseStringUTFChars",
                    171: "JNIEnv.GetArrayLength", 173: "JNIEnv.GetObjectArrayElement",
                    228: "JNIEnv.ExceptionCheck", 232: "JNIEnv.GetObjectRefType"}.get(
                        index, f"JNIEnv.slot[{index}]")
                   if not is_vm else f"JavaVM.slot[{index}]")
            caller = guest.reg_read(UC_ARM64_REG_X30) - 4
            arg0 = guest.reg_read(UC_ARM64_REG_X0)
            arg1 = guest.reg_read(UC_ARM64_REG_X1)
            arg2 = guest.reg_read(UC_ARM64_REG_X2)
            arg3 = guest.reg_read(UC_ARM64_REG_X3)
            env_for_thread = JNI_ENV + 8 * (active_guest_thread_id - GUEST_THREAD_ID)
            call_record = {"api": api, "caller": hex(caller)}
            jni_calls.append(call_record)
            try:
                if is_vm and index == 4:
                    if arg2:
                        try:
                            attach_version, attach_name, attach_group = struct.unpack(
                                "<I4xQQ", guest.mem_read(arg2, 24))
                            call_record["attach_args"] = {
                                "address": hex(arg2), "version": hex(attach_version),
                                "name_pointer": hex(attach_name), "group": hex(attach_group),
                                "name": (read_guest_string(guest, attach_name, 128)
                                         if attach_name else None)}
                        except (UcError, UnicodeDecodeError, ValueError) as error:
                            call_record["attach_args_error"] = str(error)
                    valid_worker_args = (active_guest_thread_id == GUEST_THREAD_ID + 1
                                         and caller == BASE + 0x017D8BA4
                                         and arg2 != 0
                                         and call_record.get("attach_args") == {
                                             "address": hex(arg2), "version": "0x10006",
                                             "name_pointer": hex(BASE + 0x002EDDD4),
                                             "group": "0x0", "name": "Mainloop"})
                    valid_worker_reattach = (active_guest_thread_id == GUEST_THREAD_ID + 1
                                             and active_guest_thread_id in attached_guest_threads
                                             and arg2 == 0)
                    valid_graphics_attach = (active_guest_thread_id == GUEST_THREAD_ID + 5
                                             and caller == BASE + 0x017D8B1C
                                             and arg2 == 0
                                             and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                                             <= arg1 <= POOL_WORKER_STACK_BASE
                                             + 4 * WORKER_STACK_SIZE - 8)
                    valid_graphics_reattach = (
                        active_guest_thread_id == GUEST_THREAD_ID + 5
                        and active_guest_thread_id in attached_guest_threads
                        and arg2 == 0
                        and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                        <= arg1 <= POOL_WORKER_STACK_BASE
                        + 4 * WORKER_STACK_SIZE - 8)
                    valid_main_args = (active_guest_thread_id == GUEST_THREAD_ID and arg2 == 0)
                    if (arg0 != JNI_VM or arg1 == 0 or arg1 & 7
                            or not (valid_worker_args or valid_worker_reattach
                                    or valid_graphics_attach or valid_graphics_reattach
                                    or valid_main_args)):
                        raise ValueError("AttachCurrentThread requires the observed per-thread VM arguments")
                    guest.mem_write(arg1, struct.pack("<Q", env_for_thread))
                    attached_guest_threads.add(active_guest_thread_id)
                    call_record["environment_out"] = hex(arg1)
                    result = 0
                elif not is_vm and index == 6:
                    if arg0 != env_for_thread or len(classes) >= 128:
                        raise ValueError("FindClass requires the tracked environment and available handles")
                    class_name = read_guest_string(guest, arg1, 256)
                    if not all(character.isalnum() or character in "/_$" for character in class_name):
                        raise ValueError("unsupported JNI class name")
                    entry = class_by_name.get(class_name)
                    if entry is None:
                        handle = JNI_CLASS_HANDLES + len(classes) * 8
                        entry = {"name": class_name, "handle": hex(handle), "methods": []}
                        classes.append(entry)
                        class_by_name[class_name] = entry
                        class_by_handle[handle] = entry
                    call_record["class"] = class_name
                    result = int(entry["handle"], 16)
                elif not is_vm and index == 215:
                    if arg0 != env_for_thread or arg1 not in class_by_handle or arg2 == 0 or arg2 & 7:
                        raise ValueError("RegisterNatives requires tracked environment, class, aligned table")
                    if not 0 < arg3 <= 128:
                        raise ValueError("RegisterNatives method count exceeds diagnostic bound")
                    entry = class_by_handle[arg1]
                    methods = []
                    for method_index in range(arg3):
                        name_ptr, signature_ptr, function_ptr = struct.unpack(
                            "<QQQ", guest.mem_read(arg2 + method_index * 24, 24))
                        method_name = read_guest_string(guest, name_ptr, 128)
                        signature = read_guest_string(guest, signature_ptr, 512)
                        if not signature.startswith("(") or ")" not in signature:
                            raise ValueError("invalid native method signature")
                        if not BASE <= function_ptr < BASE + libg_object["image_size"] or function_ptr & 3:
                            raise ValueError("native function pointer is outside aligned libg image")
                        repeated = any(item["name"] == method_name and item["signature"] == signature
                                       for item in entry["methods"] + methods)
                        method = {"name": method_name, "signature": signature,
                                  "function_pointer": hex(function_ptr),
                                  "libg_offset": hex(function_ptr - BASE)}
                        if repeated:
                            # ART registers entries in order, so a repeated name/signature
                            # replaces the previous native pointer rather than failing.
                            method["replaces_previous_registration"] = True
                        methods.append(method)
                    entry["methods"].extend(methods)
                    call_record["class"] = entry["name"]
                    call_record["method_count"] = len(methods)
                    result = 0
                elif not is_vm and index == 228:
                    if arg0 != env_for_thread:
                        raise ValueError("ExceptionCheck requires the tracked environment")
                    call_record["pending_exception"] = False
                    result = 0
                elif not is_vm and index == 19:
                    frames = jni_local_frames.setdefault(active_guest_thread_id, [])
                    if (arg0 != env_for_thread or active_guest_thread_id != GUEST_THREAD_ID + 1
                            or caller != BASE + 0x0139E598 or arg1 != 256
                            or len(frames) >= 8):
                        raise ValueError("PushLocalFrame requires observed Mainloop capacity and caller")
                    frames.append(issued_jni_local_refs.copy())
                    call_record.update({"capacity": arg1, "frame_depth": len(frames)})
                    result = 0
                elif not is_vm and index == 20:
                    frames = jni_local_frames.get(active_guest_thread_id, [])
                    if arg0 != env_for_thread or not frames or arg1 != 0:
                        raise ValueError("PopLocalFrame requires tracked frame and null result")
                    prior_refs = frames.pop()
                    issued_jni_local_refs.clear()
                    issued_jni_local_refs.update(prior_refs)
                    call_record.update({"frame_depth": len(frames),
                                        "released_frame_local_references": True})
                    result = 0
                elif not is_vm and index == 21:
                    if arg0 != env_for_thread or (arg1 not in tracked_global_ref_handles
                                           and arg1 not in class_by_handle
                                           and arg1 != JNI_OPAQUE_ASSET_MANAGER_LOCAL):
                        raise ValueError("NewGlobalRef requires tracked environment and opaque object")
                    if arg1 == JNI_OPAQUE_ASSET_MANAGER_LOCAL:
                        if issued_jni_local_refs.get(arg1, 0) < 1:
                            raise ValueError("AssetManager local reference is not active")
                        result = JNI_OPAQUE_ASSET_MANAGER
                        call_record["object"] = "AssetManager"
                        call_record["source_local_reference"] = hex(arg1)
                    else:
                        result = arg1
                        call_record["object"] = (tracked_global_ref_handles[arg1]
                                                 if arg1 in tracked_global_ref_handles else
                                                 "jclass:" + class_by_handle[arg1]["name"])
                        call_record["pass_through"] = True
                    issued_jni_global_refs[result] = issued_jni_global_refs.get(result, 0) + 1
                    call_record["active_global_references"] = issued_jni_global_refs[result]
                elif not is_vm and index == 22:
                    if (arg0 != env_for_thread or arg1 != JNI_OPAQUE_ACTIVITY
                            or issued_jni_global_refs.get(arg1, 0) < 1
                            or caller != BASE + 0x017D8ABC):
                        raise ValueError("DeleteGlobalRef requires the observed Activity global reference")
                    count_after = issued_jni_global_refs[arg1] - 1
                    if count_after:
                        issued_jni_global_refs[arg1] = count_after
                    else:
                        del issued_jni_global_refs[arg1]
                    call_record.update({"object": "Activity", "active_global_references": count_after})
                    result = arg0  # Void JNI call.
                elif not is_vm and index == 232:
                    if arg0 != env_for_thread:
                        raise ValueError("GetObjectRefType requires the tracked environment")
                    if arg1 == JNI_OPAQUE_ACTIVITY and arg1 in issued_jni_global_refs:
                        call_record.update({"object": "Activity", "reference_type": "JNIGlobalRefType",
                                            "basis": "tracked successful NewGlobalRef call"})
                        result = 2
                    elif (arg1 in (JNI_OPAQUE_PACKAGE_MANAGER, JNI_OPAQUE_ASSET_MANAGER_LOCAL)
                          and arg1 in issued_jni_local_refs
                          and arg1 not in issued_jni_global_refs):
                        call_record.update({"object": ("PackageManager" if arg1 == JNI_OPAQUE_PACKAGE_MANAGER
                                                       else "AssetManager"),
                                            "reference_type": "JNILocalRefType",
                                            "basis": "tracked CallObjectMethodV return"})
                        result = 1
                    elif (arg1 in (JNI_OPAQUE_WINDOW_MANAGER, JNI_OPAQUE_DISPLAY)
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and issued_jni_local_refs.get(arg1, 0) > 0
                          and arg1 not in issued_jni_global_refs):
                        call_record.update({"object": ("WindowManager"
                                                       if arg1 == JNI_OPAQUE_WINDOW_MANAGER
                                                       else "Display"),
                                            "reference_type": "JNILocalRefType",
                                            "basis": "tracked graphics JNI object return"})
                        result = 1
                    elif (arg1 == JNI_OPAQUE_LAUNCH_INTENT
                          and issued_jni_local_refs.get(arg1, 0) > 0):
                        call_record.update({"object": "launcher Intent",
                                            "reference_type": "JNILocalRefType",
                                            "basis": "tracked Activity.getIntent return"})
                        result = 1
                    else:
                        raise ValueError("GetObjectRefType requires a tracked reference of known type")
                elif not is_vm and index == 25:
                    object_label = {
                        (JNI_OPAQUE_PACKAGE_MANAGER, BASE + 0x013FA984): "PackageManager",
                        (JNI_OPAQUE_ASSET_MANAGER_LOCAL, BASE + 0x0140D5D8): "AssetManager",
                        (JNI_OPAQUE_LAUNCH_INTENT, BASE + 0x0141B600): "launcher Intent",
                        (JNI_OPAQUE_WINDOW_MANAGER, BASE + 0x013F77C8): "WindowManager",
                        (JNI_OPAQUE_WINDOW_MANAGER, BASE + 0x0140CDB0): "WindowManager",
                    }.get((arg1, caller))
                    if (object_label is None and active_guest_thread_id == GUEST_THREAD_ID + 5
                            and issued_jni_local_refs.get(arg1, 0) > 0):
                        object_label = {JNI_OPAQUE_WINDOW_MANAGER: "WindowManager",
                                        JNI_OPAQUE_DISPLAY: "Display"}.get(arg1)
                    if (arg0 != env_for_thread or object_label is None
                            or issued_jni_local_refs.get(arg1, 0) < 1):
                        raise ValueError("NewLocalRef requires an observed tracked local reference")
                    call_record.update({"object": object_label, "pass_through": True})
                    issued_jni_local_refs[arg1] += 1
                    call_record["active_local_references"] = issued_jni_local_refs[arg1]
                    result = arg1
                elif not is_vm and index == 23:
                    allowed_deletions = {
                        (JNI_OPAQUE_PACKAGE_MANAGER, BASE + 0x0141B564): "PackageManager",
                        (JNI_OPAQUE_PACKAGE_MANAGER, BASE + 0x017D8ABC): "PackageManager",
                        (JNI_OPAQUE_ASSET_MANAGER_LOCAL, BASE + 0x0141B3C8): "AssetManager",
                        (JNI_OPAQUE_ASSET_MANAGER_LOCAL, BASE + 0x017D8ABC): "AssetManager",
                        (JNI_OPAQUE_APP_VERSION, BASE + 0x013FED20): "APK package versionName",
                        (JNI_OPAQUE_HPE_FEATURE, BASE + 0x013FACC4): "HPE_EXPERIENCE feature String",
                        (JNI_OPAQUE_LAUNCH_INTENT, BASE + 0x01419CB8): "launcher Intent",
                        (JNI_OPAQUE_LAUNCH_INTENT, BASE + 0x017D8ABC): "launcher Intent",
                        (JNI_OPAQUE_USER_ID_EXTRA, BASE + 0x0141BB70): "launcher Intent userId key",
                        (JNI_OPAQUE_WINDOW_MANAGER, BASE + 0x013F73A4): "WindowManager",
                        (JNI_OPAQUE_WINDOW_MANAGER, BASE + 0x017D8ABC): "WindowManager",
                        (JNI_OPAQUE_WINDOW_SERVICE_NAME, BASE + 0x0141B1C4): "window service name",
                    }
                    object_label = allowed_deletions.get((arg1, caller))
                    if (object_label is None and active_guest_thread_id == GUEST_THREAD_ID + 5
                            and issued_jni_local_refs.get(arg1, 0) > 0):
                        object_label = {JNI_OPAQUE_WINDOW_MANAGER: "WindowManager",
                                        JNI_OPAQUE_DISPLAY: "Display"}.get(arg1)
                    if (object_label is None and caller == BASE + 0x0140DC10
                            and tracked_guest_strings.get(arg1, {}).get("label")
                            == "AssetManager.list element"):
                        object_label = "AssetManager.list element"
                    if (object_label is None and caller == BASE + 0x0140DC54
                            and arg1 in tracked_guest_arrays):
                        object_label = "AssetManager.list array"
                    if (object_label is None and caller == BASE + 0x0140DA10
                            and tracked_guest_strings.get(arg1, {}).get("label")
                            == "AssetManager path"):
                        object_label = "AssetManager path"
                    if (object_label is None and caller == BASE + 0x0140C9F4
                            and tracked_guest_strings.get(arg1, {}).get("label")
                            == "ApplicationUtilBase preference key"):
                        object_label = "ApplicationUtilBase preference key"
                    if (object_label is None and caller == BASE + 0x013FED20
                            and tracked_guest_strings.get(arg1, {}).get("label")
                            == "ApplicationUtilBase preference value"):
                        object_label = "ApplicationUtilBase preference value"
                    if (object_label is None and caller in (BASE + 0x0140BB6C,
                                                            BASE + 0x0140BB74)
                            and tracked_guest_strings.get(arg1, {}).get("label")
                            in ("ApplicationUtilBase store key",
                                "ApplicationUtilBase store value")):
                        object_label = tracked_guest_strings[arg1]["label"]
                    if (arg0 != env_for_thread or object_label is None
                            or issued_jni_local_refs.get(arg1, 0) < 1):
                        raise ValueError("DeleteLocalRef requires an observed tracked local reference")
                    issued_jni_local_refs[arg1] -= 1
                    call_record.update({"object": object_label,
                                        "active_local_references": issued_jni_local_refs[arg1]})
                    result = arg0  # Void JNI call.
                elif not is_vm and index == 33:
                    class_name = (class_by_handle[arg1]["name"] if arg1 in class_by_handle
                                  else None)
                    method_name = read_guest_string(guest, arg2, 128)
                    signature = read_guest_string(guest, arg3, 512)
                    call_record.update({"class": class_name, "method_name": method_name,
                                        "signature": signature})
                    if (arg0 == env_for_thread and class_name == "android/content/Context"
                            and method_name == "getPackageManager"
                            and signature == "()Landroid/content/pm/PackageManager;"
                            and arg1 in issued_jni_global_refs):
                        issued_context_method_ids.add(JNI_CONTEXT_GET_PM_METHOD_ID)
                        result = JNI_CONTEXT_GET_PM_METHOD_ID
                    elif (arg0 == env_for_thread
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and class_name == "android/content/Context"
                          and method_name == "getSystemService"
                          and signature == "(Ljava/lang/String;)Ljava/lang/Object;"
                          and caller == BASE + 0x0141B130
                          and arg1 in issued_jni_global_refs):
                        issued_context_method_ids.add(JNI_CONTEXT_GET_SYSTEM_SERVICE_METHOD_ID)
                        result = JNI_CONTEXT_GET_SYSTEM_SERVICE_METHOD_ID
                    elif (arg0 == env_for_thread
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and class_name == "android/view/WindowManager"
                          and method_name == "getDefaultDisplay"
                          and signature == "()Landroid/view/Display;"
                          and caller == BASE + 0x0140CF74
                          and arg1 in issued_jni_global_refs):
                        issued_context_method_ids.add(JNI_WINDOW_MANAGER_GET_DISPLAY_METHOD_ID)
                        result = JNI_WINDOW_MANAGER_GET_DISPLAY_METHOD_ID
                    elif (arg0 == env_for_thread
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and class_name == "android/view/Display"
                          and method_name == "getRefreshRate"
                          and signature == "()F"
                          and caller == BASE + 0x014020C8
                          and arg1 in issued_jni_global_refs):
                        issued_context_method_ids.add(JNI_DISPLAY_GET_REFRESH_RATE_METHOD_ID)
                        result = JNI_DISPLAY_GET_REFRESH_RATE_METHOD_ID
                    elif (arg0 == JNI_ENV and class_name == "android/app/Activity"
                          and method_name == "getIntent"
                          and signature == "()Landroid/content/Intent;"
                          and caller == BASE + 0x01419A18):
                        issued_activity_intent_method_ids.add(JNI_ACTIVITY_GET_INTENT_METHOD_ID)
                        result = JNI_ACTIVITY_GET_INTENT_METHOD_ID
                    elif (arg0 == JNI_ENV and class_name == "android/content/Intent"
                          and method_name == "hasExtra"
                          and signature == "(Ljava/lang/String;)Z"
                          and caller == BASE + 0x0141BADC):
                        issued_intent_has_extra_method_ids.add(JNI_INTENT_HAS_EXTRA_METHOD_ID)
                        result = JNI_INTENT_HAS_EXTRA_METHOD_ID
                    elif (arg0 == JNI_ENV and class_name == "android/content/Intent"
                          and method_name == "getFlags"
                          and signature == "()I"
                          and caller == BASE + 0x0141B7C8):
                        issued_intent_get_flags_method_ids.add(JNI_INTENT_GET_FLAGS_METHOD_ID)
                        result = JNI_INTENT_GET_FLAGS_METHOD_ID
                    elif (arg0 == WORKER_JNI_ENV and class_name == "android/content/Context"
                          and method_name == "getAssets"
                          and signature == "()Landroid/content/res/AssetManager;"
                          and caller == BASE + 0x0141ABD4
                          and arg1 in issued_jni_global_refs):
                        issued_context_assets_method_ids.add(JNI_CONTEXT_GET_ASSETS_METHOD_ID)
                        result = JNI_CONTEXT_GET_ASSETS_METHOD_ID
                    elif (arg0 == WORKER_JNI_ENV
                          and class_name == "android/content/res/AssetManager"
                          and method_name == "list"
                          and signature == "(Ljava/lang/String;)[Ljava/lang/String;"
                          and caller == BASE + 0x0140D97C
                          and arg1 in issued_jni_global_refs):
                        issued_asset_manager_list_method_ids.add(JNI_ASSET_MANAGER_LIST_METHOD_ID)
                        result = JNI_ASSET_MANAGER_LIST_METHOD_ID
                    elif (arg0 == env_for_thread and class_name == "android/content/pm/PackageManager"
                          and method_name == "hasSystemFeature"
                          and signature == "(Ljava/lang/String;)Z"
                          and arg1 in issued_jni_global_refs):
                        issued_pm_method_ids.add(JNI_PM_HAS_FEATURE_METHOD_ID)
                        result = JNI_PM_HAS_FEATURE_METHOD_ID
                    else:
                        stop_jni(guest, api, caller, "instance method lookup is not supported",
                                 {"class": class_name, "method_name": method_name,
                                  "signature": signature})
                        return
                elif not is_vm and index == 35:
                    if arg2 == JNI_ASSET_MANAGER_LIST_METHOD_ID and arg3:
                        try:
                            stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                                "<QQQii", guest.mem_read(arg3, 32))
                            argument_address = gr_top + gr_offs
                            string_argument = struct.unpack(
                                "<Q", guest.mem_read(argument_address, 8))[0]
                            call_record["asset_list_va_list"] = {
                                "stack_top": hex(stack_top), "gr_top": hex(gr_top),
                                "vr_top": hex(vr_top), "gr_offs": gr_offs,
                                "vr_offs": vr_offs,
                                "argument_address": hex(argument_address),
                                "string_argument": hex(string_argument),
                                "tracked_path": tracked_guest_strings.get(string_argument)}
                        except (UcError, ValueError) as error:
                            call_record["asset_list_va_list_error"] = str(error)
                    if (arg0 == WORKER_JNI_ENV
                            and arg1 in (JNI_OPAQUE_ASSET_MANAGER_LOCAL,
                                         JNI_OPAQUE_ASSET_MANAGER)
                            and (arg1 == JNI_OPAQUE_ASSET_MANAGER
                                 or issued_jni_local_refs.get(arg1, 0) > 0)
                            and arg2 == JNI_ASSET_MANAGER_LIST_METHOD_ID
                            and arg2 in issued_asset_manager_list_method_ids
                            and caller == BASE + 0x0140DB4C
                            and assets_dir_metadata is not None
                            and "asset_list_va_list" in call_record):
                        va = call_record["asset_list_va_list"]
                        string_argument = int(va["string_argument"], 16)
                        path_string = tracked_guest_strings.get(string_argument)
                        if (va["gr_offs"] != -48 or va["vr_offs"] != -128
                                or not WORKER_STACK <= int(va["argument_address"], 16)
                                <= WORKER_STACK + WORKER_STACK_SIZE - 8
                                or path_string is None
                                or path_string["label"] != "AssetManager path"
                                or issued_jni_local_refs.get(string_argument, 0) < 1):
                            raise ValueError("AssetManager.list requires the tracked path String and va_list")
                        asset_root = Path(assets_dir_metadata["host_directory"]).resolve(strict=True)
                        asset_path = (asset_root / path_string["value"]).resolve()
                        if not asset_path.is_relative_to(asset_root):
                            raise ValueError("AssetManager.list path escapes the validated asset root")
                        entries = (sorted(item.name for item in asset_path.iterdir())
                                   if asset_path.is_dir() else [])
                        if len(entries) > 4096 or any("/" in name or "\\" in name
                                                      for name in entries):
                            raise ValueError("AssetManager.list entries exceed diagnostic bounds")
                        result = 0x69000000 + len(tracked_guest_arrays) * 8
                        tracked_guest_arrays[result] = entries
                        issued_jni_local_refs[result] = 1
                        call_record.update({"object": "AssetManager", "method_name": "list",
                                            "path": path_string["value"],
                                            "host_path": str(asset_path),
                                            "entry_count": len(entries),
                                            "first_entries": entries[:5],
                                            "ordering": "ordinal sort; diagnostic assumption",
                                            "array_handle": hex(result)})
                    elif (arg0 != env_for_thread
                            or not (arg1 == JNI_OPAQUE_ACTIVITY
                                    and arg1 in issued_jni_global_refs
                                    or arg1 == JNI_OPAQUE_WINDOW_MANAGER
                                    and issued_jni_local_refs.get(arg1, 0) > 0)
                            or arg3 == 0 or arg3 & 7):
                        raise ValueError("CallObjectMethodV requires a tracked receiver and va_list")
                    elif (arg1 == JNI_OPAQUE_WINDOW_MANAGER
                          and arg2 == JNI_WINDOW_MANAGER_GET_DISPLAY_METHOD_ID
                          and arg2 in issued_context_method_ids
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and caller == BASE + 0x0140D1AC
                          and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                          <= arg3 <= POOL_WORKER_STACK_BASE
                          + 4 * WORKER_STACK_SIZE - 32):
                        guest.mem_read(arg3, 32)
                        issued_jni_local_refs[JNI_OPAQUE_DISPLAY] = 1
                        call_record.update({"object": "WindowManager",
                                            "method_name": "getDefaultDisplay",
                                            "return_basis": "virtual display for observed Surface dimensions"})
                        result = JNI_OPAQUE_DISPLAY
                    elif (arg2 == JNI_CONTEXT_GET_SYSTEM_SERVICE_METHOD_ID
                          and arg2 in issued_context_method_ids
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and caller == BASE + 0x013F7378
                          and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                          <= arg3 <= POOL_WORKER_STACK_BASE
                          + 4 * WORKER_STACK_SIZE - 32):
                        stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                            "<QQQii", guest.mem_read(arg3, 32))
                        argument_address = gr_top + gr_offs
                        service_name = struct.unpack(
                            "<Q", guest.mem_read(argument_address, 8))[0]
                        call_record["window_service_va_list"] = {
                            "stack_top": hex(stack_top), "gr_top": hex(gr_top),
                            "vr_top": hex(vr_top), "gr_offs": gr_offs,
                            "vr_offs": vr_offs,
                            "argument_address": hex(argument_address),
                            "string_handle": hex(service_name),
                            "tracked_string": tracked_guest_strings.get(service_name),
                        }
                        if (service_name != JNI_OPAQUE_WINDOW_SERVICE_NAME
                                or gr_offs != -48 or vr_offs != -128
                                or tracked_guest_strings.get(service_name, {}).get("value")
                                != "window"
                                or issued_jni_local_refs.get(service_name, 0) < 1):
                            raise ValueError("getSystemService requires the observed window String")
                        issued_jni_local_refs[JNI_OPAQUE_WINDOW_MANAGER] = 1
                        call_record.update({"object": "Activity",
                                            "method_name": "getSystemService",
                                            "argument": "window",
                                            "argument_address": hex(argument_address),
                                            "return_basis": "virtual WindowManager for observed graphics initialization"})
                        result = JNI_OPAQUE_WINDOW_MANAGER
                    elif (arg2 == JNI_CONTEXT_GET_PM_METHOD_ID
                            and arg2 in issued_context_method_ids
                            and caller == BASE + 0x0141B538):
                        call_record.update({"object": "Activity", "method_name": "getPackageManager",
                                            "signature": "()Landroid/content/pm/PackageManager;",
                                            "va_list": hex(arg3),
                                            "return_basis": "opaque package manager of the controlled guest application"})
                        tracked_global_ref_handles[JNI_OPAQUE_PACKAGE_MANAGER] = "PackageManager"
                        issued_jni_local_refs[JNI_OPAQUE_PACKAGE_MANAGER] = 1
                        result = JNI_OPAQUE_PACKAGE_MANAGER
                    elif (arg2 == JNI_CONTEXT_GET_ASSETS_METHOD_ID
                          and arg2 in issued_context_assets_method_ids
                          and caller == BASE + 0x0141B39C
                          and WORKER_STACK <= arg3 <= WORKER_STACK + WORKER_STACK_SIZE - 32):
                        call_record.update({"object": "Activity", "method_name": "getAssets",
                                            "signature": "()Landroid/content/res/AssetManager;",
                                            "va_list": hex(arg3),
                                            "return_basis": "tracked opaque AssetManager from createGameMain"})
                        issued_jni_local_refs[JNI_OPAQUE_ASSET_MANAGER_LOCAL] = (
                            issued_jni_local_refs.get(JNI_OPAQUE_ASSET_MANAGER_LOCAL, 0) + 1)
                        result = JNI_OPAQUE_ASSET_MANAGER_LOCAL
                    elif (arg2 == JNI_ACTIVITY_GET_INTENT_METHOD_ID
                          and arg2 in issued_activity_intent_method_ids
                          and caller == BASE + 0x01419C8C
                          and STACK <= arg3 <= STACK + STACK_SIZE - 32):
                        call_record.update({"object": "Activity", "method_name": "getIntent",
                                            "signature": "()Landroid/content/Intent;",
                                            "return_basis": "controlled launcher intent with no deep link"})
                        issued_jni_local_refs[JNI_OPAQUE_LAUNCH_INTENT] = (
                            issued_jni_local_refs.get(JNI_OPAQUE_LAUNCH_INTENT, 0) + 1)
                        result = JNI_OPAQUE_LAUNCH_INTENT
                    else:
                        raise ValueError("only observed Context method invocations are supported")
                elif not is_vm and index == 167:
                    string_text = read_guest_string(guest, arg1, 256)
                    if (arg0 == env_for_thread and caller == BASE + 0x013FAC94
                            and string_text == "com.google.android.play.feature.HPE_EXPERIENCE"
                            and JNI_OPAQUE_HPE_FEATURE not in tracked_guest_strings):
                        string_handle = JNI_OPAQUE_HPE_FEATURE
                        string_label = "HPE_EXPERIENCE feature"
                    elif (arg0 == env_for_thread and caller == BASE + 0x013D07F4
                          and arg1 == BASE + 0x002BA599 and len(string_text) == 40
                          and JNI_OPAQUE_MAIN_RESULT not in tracked_guest_strings):
                        string_handle = JNI_OPAQUE_MAIN_RESULT
                        string_label = "createGameMain result"
                    elif (arg0 == WORKER_JNI_ENV and caller == BASE + 0x0140D9E0
                          and assets_dir_metadata is not None and 0 < len(string_text) <= 240
                          and not string_text.startswith("/") and "\\" not in string_text
                          and ":" not in string_text
                          and all(part not in ("", ".", "..")
                                  for part in string_text.split("/"))):
                        string_handle = 0x68001000 + len(tracked_guest_strings) * 8
                        if string_handle in tracked_guest_strings:
                            raise ValueError("asset path String handle collision")
                        string_label = "AssetManager path"
                    elif (arg0 == JNI_ENV and caller == BASE + 0x0141BB40
                          and string_text == "userId"
                          and STACK <= arg1 < STACK + STACK_SIZE):
                        string_handle = JNI_OPAQUE_USER_ID_EXTRA
                        string_label = "launcher Intent extra key"
                    elif (arg0 == env_for_thread
                          and active_guest_thread_id == GUEST_THREAD_ID + 5
                          and caller == BASE + 0x0141B194
                          and string_text == "window"
                          and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                          <= arg1 < POOL_WORKER_STACK_BASE + 4 * WORKER_STACK_SIZE):
                        string_handle = JNI_OPAQUE_WINDOW_SERVICE_NAME
                        string_label = "window service name"
                    elif (arg0 == WORKER_JNI_ENV
                          and active_guest_thread_id == GUEST_THREAD_ID + 1
                          and caller == BASE + 0x0140C9A4
                          and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", string_text)):
                        string_handle = 0x68001000 + len(tracked_guest_strings) * 8
                        if string_handle in tracked_guest_strings:
                            raise ValueError("preference key String handle collision")
                        string_label = "ApplicationUtilBase preference key"
                    elif (arg0 == WORKER_JNI_ENV
                          and active_guest_thread_id == GUEST_THREAD_ID + 1
                          and caller in (BASE + 0x013F61A4, BASE + 0x013F61F8)
                          and string_text.isascii()
                          and len(string_text) <= 240
                          and (caller == BASE + 0x013F61F8
                               or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", string_text))):
                        string_handle = 0x68001000 + len(tracked_guest_strings) * 8
                        if string_handle in tracked_guest_strings:
                            raise ValueError("preference store String handle collision")
                        string_label = ("ApplicationUtilBase store key"
                                        if caller == BASE + 0x013F61A4
                                        else "ApplicationUtilBase store value")
                    else:
                        stop_jni(guest, api, caller, "only observed pinned NewStringUTF inputs are supported",
                                 {"text": string_text})
                        return
                    tracked_guest_strings[string_handle] = {
                        "label": string_label, "value": string_text}
                    issued_jni_local_refs[string_handle] = 1
                    call_record.update({"text": string_text, "reference_type": "JNILocalRefType",
                                        "string_label": string_label})
                    result = string_handle
                elif not is_vm and index == 171:
                    array = tracked_guest_arrays.get(arg1)
                    if (arg0 != WORKER_JNI_ENV or array is None
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or caller != BASE + 0x0140DBB0):
                        raise ValueError("GetArrayLength requires the tracked AssetManager.list result")
                    result = len(array)
                    call_record.update({"array_handle": hex(arg1), "length": result})
                elif not is_vm and index == 173:
                    array = tracked_guest_arrays.get(arg1)
                    if (arg0 != WORKER_JNI_ENV or array is None
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or caller != BASE + 0x0140DC08
                            or arg2 >= len(array)):
                        raise ValueError("GetObjectArrayElement requires a tracked array and in-range index")
                    result = 0x68001000 + len(tracked_guest_strings) * 8
                    if result in tracked_guest_strings:
                        raise ValueError("array element String handle collision")
                    tracked_guest_strings[result] = {
                        "label": "AssetManager.list element", "value": array[arg2]}
                    issued_jni_local_refs[result] = 1
                    call_record.update({"array_handle": hex(arg1), "index": arg2,
                                        "element_name": array[arg2], "string_handle": hex(result)})
                elif not is_vm and index == 169:
                    guest_string = tracked_guest_strings.get(arg1)
                    if (arg0 != WORKER_JNI_ENV or guest_string is None
                            or guest_string["label"] not in (
                                "AssetManager.list element", "APK package versionName",
                                "ApplicationUtilBase preference value")
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or caller != BASE + 0x013950F0
                            or not guest_string["value"].isascii()
                            or "\0" in guest_string["value"]):
                        raise ValueError("GetStringUTFChars requires a tracked ASCII guest String")
                    utf8 = guest_string["value"].encode("ascii")
                    result = allocate(len(utf8) + 1)
                    if result == 0:
                        raise ValueError("guest heap exhausted for asset entry UTF-8 copy")
                    guest.mem_write(result, utf8 + b"\0")
                    if arg2:
                        guest.mem_write(arg2, b"\x01")
                    active_string_utf_chars[result] = {"string_handle": arg1,
                                                       "value": guest_string["value"]}
                    call_record.update({"string_handle": hex(arg1),
                                        "text": guest_string["value"],
                                        "utf8_buffer": hex(result),
                                        "is_copy_pointer": hex(arg2)})
                elif not is_vm and index == 170:
                    active = active_string_utf_chars.get(arg2)
                    if (arg0 != WORKER_JNI_ENV or active is None
                            or active["string_handle"] != arg1):
                        raise ValueError("ReleaseStringUTFChars requires a matching active copy")
                    call_record.update({"string_handle": hex(arg1),
                                        "utf8_buffer": hex(arg2),
                                        "text": active["value"]})
                    del active_string_utf_chars[arg2]
                    result = arg0  # Void JNI call.
                elif not is_vm and index == 38 and arg2 == JNI_INTENT_HAS_EXTRA_METHOD_ID:
                    if (arg0 != JNI_ENV or arg1 != JNI_OPAQUE_LAUNCH_INTENT
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or arg2 not in issued_intent_has_extra_method_ids
                            or caller != BASE + 0x013F7718
                            or not STACK <= arg3 <= STACK + STACK_SIZE - 32):
                        raise ValueError("Intent.hasExtra requires tracked launcher Intent")
                    stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                        "<QQQii", guest.mem_read(arg3, 32))
                    argument_address = gr_top + gr_offs
                    if (gr_offs != -48 or vr_offs != -128
                            or not STACK <= stack_top < STACK + STACK_SIZE
                            or not STACK <= gr_top < STACK + STACK_SIZE
                            or not STACK <= vr_top < STACK + STACK_SIZE
                            or not STACK <= argument_address <= STACK + STACK_SIZE - 8):
                        raise ValueError("unexpected AAPCS64 va_list layout for Intent.hasExtra")
                    extra_key = struct.unpack("<Q", guest.mem_read(argument_address, 8))[0]
                    if (extra_key != JNI_OPAQUE_USER_ID_EXTRA
                            or issued_jni_local_refs.get(extra_key, 0) < 1
                            or tracked_guest_strings[extra_key]["value"] != "userId"):
                        raise ValueError("Intent.hasExtra requires the tracked userId key")
                    call_record.update({"object": "launcher Intent", "method_name": "hasExtra",
                                        "key": "userId", "return_basis":
                                        "controlled launcher Intent has no account extra"})
                    result = 0
                elif not is_vm and index == 38:
                    if (arg0 != env_for_thread or arg1 != JNI_OPAQUE_PACKAGE_MANAGER
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or arg2 != JNI_PM_HAS_FEATURE_METHOD_ID
                            or arg2 not in issued_pm_method_ids
                            or caller != BASE + 0x013F7718
                            or not STACK <= arg3 <= STACK + STACK_SIZE - 32):
                        raise ValueError("only tracked PackageManager.hasSystemFeature invocation is supported")
                    stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                        "<QQQii", guest.mem_read(arg3, 32))
                    argument_address = gr_top + gr_offs
                    if (gr_offs != -48 or vr_offs != -128
                            or not STACK <= stack_top < STACK + STACK_SIZE
                            or not STACK <= gr_top < STACK + STACK_SIZE
                            or not STACK <= vr_top < STACK + STACK_SIZE
                            or not STACK <= argument_address <= STACK + STACK_SIZE - 8):
                        raise ValueError("unexpected AAPCS64 va_list layout for hasSystemFeature")
                    feature_handle = struct.unpack("<Q", guest.mem_read(argument_address, 8))[0]
                    if (feature_handle != JNI_OPAQUE_HPE_FEATURE
                            or issued_jni_local_refs.get(feature_handle, 0) < 1):
                        raise ValueError("hasSystemFeature argument is not the tracked HPE feature String")
                    call_record.update({"object": "PackageManager", "method_name": "hasSystemFeature",
                                        "feature": tracked_guest_strings[feature_handle]["value"],
                                        "va_list": hex(arg3),
                                        "va_list_gr_offs": gr_offs,
                                        "feature_argument_address": hex(argument_address),
                                        "return_basis": "feature absent from controlled guest feature registry"})
                    result = 0
                elif not is_vm and index == 50:
                    if (arg0 != JNI_ENV or arg1 != JNI_OPAQUE_LAUNCH_INTENT
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or arg2 != JNI_INTENT_GET_FLAGS_METHOD_ID
                            or arg2 not in issued_intent_get_flags_method_ids
                            or caller != BASE + 0x014006B4
                            or not STACK <= arg3 <= STACK + STACK_SIZE - 32):
                        raise ValueError("CallIntMethodV requires observed launcher Intent.getFlags")
                    guest.mem_read(arg3, 32)
                    call_record.update({"object": "launcher Intent", "method_name": "getFlags",
                                        "return_basis": "controlled launcher Intent has zero flags"})
                    result = 0
                elif not is_vm and index == 56:
                    if (arg0 != env_for_thread
                            or active_guest_thread_id != GUEST_THREAD_ID + 5
                            or arg1 != JNI_OPAQUE_DISPLAY
                            or issued_jni_local_refs.get(arg1, 0) < 1
                            or arg2 != JNI_DISPLAY_GET_REFRESH_RATE_METHOD_ID
                            or arg2 not in issued_context_method_ids
                            or caller != BASE + 0x0140222C
                            or not POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                            <= arg3 <= POOL_WORKER_STACK_BASE
                            + 4 * WORKER_STACK_SIZE - 32):
                        raise ValueError("CallFloatMethodV requires tracked Display.getRefreshRate")
                    guest.mem_read(arg3, 32)
                    refresh_hz = 60.0
                    guest.reg_write(UC_ARM64_REG_S0, struct.unpack(
                        "<I", struct.pack("<f", refresh_hz))[0])
                    call_record.update({"object": "Display",
                                        "method_name": "getRefreshRate",
                                        "refresh_hz": refresh_hz,
                                        "basis": "controlled headless virtual display"})
                    result = 0
                elif not is_vm and index == 113:
                    class_name = (class_by_handle[arg1]["name"] if arg1 in class_by_handle
                                  else None)
                    method_name = read_guest_string(guest, arg2, 128)
                    signature = read_guest_string(guest, arg3, 512)
                    call_record.update({"class": class_name, "method_name": method_name,
                                        "signature": signature})
                    if (arg0 != env_for_thread or class_name != "com/supercell/titan/GameApp"
                            or method_name != "isEmulator" or signature != "()Z"
                            or emulator_bytecode_source is None):
                        if (arg0 == env_for_thread
                                and active_guest_thread_id == GUEST_THREAD_ID + 5
                                and class_name == "com/supercell/titan/GameApp"
                                and method_name == "getInstance"
                                and signature == "()Lcom/supercell/titan/GameApp;"
                                and caller == BASE + 0x013FB5D8):
                            issued_static_method_ids.add(JNI_GAME_APP_GET_INSTANCE_METHOD_ID)
                            result = JNI_GAME_APP_GET_INSTANCE_METHOD_ID
                            call_record["apk_bytecode_source"] = (
                                ".local-runtime/build-20260923-224545/decoded/smali/"
                                "com/supercell/titan/GameApp.smali:479")
                        elif (arg0 == WORKER_JNI_ENV
                                and class_name == "com/supercell/titan/ApplicationUtilBase"
                                and method_name == "getAppVersion"
                                and signature == "()Ljava/lang/String;"
                                and caller == BASE + 0x0140B34C):
                            issued_app_version_method_ids.add(JNI_APP_VERSION_METHOD_ID)
                            result = JNI_APP_VERSION_METHOD_ID
                            call_record["apk_bytecode_source"] = (
                                ".local-runtime/build-20260923-224545/decoded/smali/"
                                "com/supercell/titan/ApplicationUtilBase.smali:240")
                        elif (arg0 == WORKER_JNI_ENV
                                and class_name == "com/supercell/titan/ApplicationUtilBase"
                                and method_name == "getKeyValue"
                                and signature == "(Ljava/lang/String;)Ljava/lang/String;"
                                and caller == BASE + 0x0140C940):
                            issued_app_keyvalue_method_ids.add(JNI_APP_KEYVALUE_METHOD_ID)
                            result = JNI_APP_KEYVALUE_METHOD_ID
                            call_record["apk_bytecode_source"] = (
                                ".local-runtime/build-20260923-224545/decoded/smali/"
                                "com/supercell/titan/ApplicationUtilBase.smali:349")
                        elif (arg0 == WORKER_JNI_ENV
                                and class_name == "com/supercell/titan/ApplicationUtilBase"
                                and method_name == "storeKeyValue"
                                and signature == "(Ljava/lang/String;Ljava/lang/String;)V"
                                and caller == BASE + 0x0140BB04):
                            issued_app_store_keyvalue_method_ids.add(
                                JNI_APP_STORE_KEYVALUE_METHOD_ID)
                            result = JNI_APP_STORE_KEYVALUE_METHOD_ID
                            call_record["apk_bytecode_source"] = (
                                ".local-runtime/build-20260923-224545/decoded/smali/"
                                "com/supercell/titan/ApplicationUtilBase.smali:1804")
                        else:
                            stop_jni(guest, api, caller,
                                     "only APK-verified static method lookups are supported",
                                     {"class": class_name, "method_name": method_name,
                                      "signature": signature})
                            return
                    else:
                        issued_static_method_ids.add(JNI_EMULATOR_METHOD_ID)
                        call_record["apk_bytecode_source"] = emulator_bytecode_source
                        result = JNI_EMULATOR_METHOD_ID
                elif not is_vm and index == 115:
                    class_name = (class_by_handle[arg1]["name"] if arg1 in class_by_handle
                                  else None)
                    if (arg0 == WORKER_JNI_ENV
                            and active_guest_thread_id == GUEST_THREAD_ID + 1
                            and class_name == "com/supercell/titan/ApplicationUtilBase"
                            and arg2 == JNI_APP_KEYVALUE_METHOD_ID
                            and arg2 in issued_app_keyvalue_method_ids
                            and caller == BASE + 0x013FED08
                            and WORKER_STACK <= arg3 <= WORKER_STACK + WORKER_STACK_SIZE - 32):
                        stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                            "<QQQii", guest.mem_read(arg3, 32))
                        argument_address = gr_top + gr_offs
                        if (gr_offs != -48 or vr_offs != -128
                                or not WORKER_STACK <= stack_top < WORKER_STACK + WORKER_STACK_SIZE
                                or not WORKER_STACK <= gr_top < WORKER_STACK + WORKER_STACK_SIZE
                                or not WORKER_STACK <= vr_top < WORKER_STACK + WORKER_STACK_SIZE
                                or not WORKER_STACK <= argument_address
                                <= WORKER_STACK + WORKER_STACK_SIZE - 8):
                            raise ValueError("unexpected AAPCS64 va_list for getKeyValue")
                        key_handle = struct.unpack(
                            "<Q", guest.mem_read(argument_address, 8))[0]
                        key = tracked_guest_strings.get(key_handle)
                        if (key is None or key["label"] != "ApplicationUtilBase preference key"
                                or issued_jni_local_refs.get(key_handle, 0) < 1):
                            raise ValueError("getKeyValue requires a tracked preference key String")
                        preference_value = guest_preferences.get(key["value"], "")
                        string_handle = 0x68001000 + len(tracked_guest_strings) * 8
                        if string_handle in tracked_guest_strings:
                            raise ValueError("preference value String handle collision")
                        tracked_guest_strings[string_handle] = {
                            "label": "ApplicationUtilBase preference value",
                            "value": preference_value}
                        issued_jni_local_refs[string_handle] = 1
                        call_record.update({"class": class_name,
                                            "method_name": "getKeyValue",
                                            "key": key["value"],
                                            "value": preference_value,
                                            "basis": "controlled guest preference store; APK c/a.j returns empty when absent",
                                            "apk_bytecode_source": (
                                                ".local-runtime/build-20260923-224545/decoded/"
                                                "smali/com/supercell/titan/ApplicationUtilBase.smali:349")})
                        result = string_handle
                    elif (arg0 == env_for_thread
                            and active_guest_thread_id == GUEST_THREAD_ID + 5
                            and class_name == "com/supercell/titan/GameApp"
                            and arg2 == JNI_GAME_APP_GET_INSTANCE_METHOD_ID
                            and arg2 in issued_static_method_ids
                            and caller == BASE + 0x013FE354
                            and POOL_WORKER_STACK_BASE + 3 * WORKER_STACK_SIZE
                            <= arg3 <= POOL_WORKER_STACK_BASE
                            + 4 * WORKER_STACK_SIZE - 32
                            and issued_jni_global_refs.get(JNI_OPAQUE_ACTIVITY, 0) > 0):
                        guest.mem_read(arg3, 32)
                        issued_jni_local_refs[JNI_OPAQUE_ACTIVITY] = (
                            issued_jni_local_refs.get(JNI_OPAQUE_ACTIVITY, 0) + 1)
                        call_record.update({"class": class_name,
                                            "method_name": "getInstance",
                                            "source": "GameApp.q assigned the Activity in APK onCreate",
                                            "apk_bytecode_source": (
                                                ".local-runtime/build-20260923-224545/decoded/"
                                                "smali/com/supercell/titan/GameApp.smali:479")})
                        result = JNI_OPAQUE_ACTIVITY
                    else:
                        if (arg0 != WORKER_JNI_ENV
                                or class_name != "com/supercell/titan/ApplicationUtilBase"
                                or arg2 != JNI_APP_VERSION_METHOD_ID
                                or arg2 not in issued_app_version_method_ids
                                or caller != BASE + 0x013FED08
                                or not WORKER_STACK <= arg3 <= WORKER_STACK + WORKER_STACK_SIZE - 32
                                or not isinstance(pinned_apk_version, str)
                                or not pinned_apk_version.isascii()):
                            raise ValueError("CallStaticObjectMethodV requires verified getAppVersion/getInstance")
                        tracked_guest_strings[JNI_OPAQUE_APP_VERSION] = {
                            "label": "APK package versionName", "value": pinned_apk_version}
                        issued_jni_local_refs[JNI_OPAQUE_APP_VERSION] = 1
                        call_record.update({"class": class_name,
                                            "method_name": "getAppVersion",
                                            "version_name": pinned_apk_version,
                                            "source": "original APK PackageInfo.versionName and pinned manifest"})
                        result = JNI_OPAQUE_APP_VERSION
                elif not is_vm and index == 142:
                    class_name = (class_by_handle[arg1]["name"] if arg1 in class_by_handle
                                  else None)
                    if (arg0 != WORKER_JNI_ENV
                            or active_guest_thread_id != GUEST_THREAD_ID + 1
                            or class_name != "com/supercell/titan/ApplicationUtilBase"
                            or arg2 != JNI_APP_STORE_KEYVALUE_METHOD_ID
                            or arg2 not in issued_app_store_keyvalue_method_ids
                            or caller != BASE + 0x013F62D4
                            or not WORKER_STACK <= arg3
                            <= WORKER_STACK + WORKER_STACK_SIZE - 32):
                        raise ValueError("CallStaticVoidMethodV requires verified storeKeyValue")
                    stack_top, gr_top, vr_top, gr_offs, vr_offs = struct.unpack(
                        "<QQQii", guest.mem_read(arg3, 32))
                    argument_address = gr_top + gr_offs
                    if (gr_offs != -48 or vr_offs != -128
                            or not WORKER_STACK <= stack_top < WORKER_STACK + WORKER_STACK_SIZE
                            or not WORKER_STACK <= gr_top < WORKER_STACK + WORKER_STACK_SIZE
                            or not WORKER_STACK <= vr_top < WORKER_STACK + WORKER_STACK_SIZE
                            or not WORKER_STACK <= argument_address
                            <= WORKER_STACK + WORKER_STACK_SIZE - 16):
                        raise ValueError("unexpected AAPCS64 va_list for storeKeyValue")
                    key_handle, value_handle = struct.unpack(
                        "<QQ", guest.mem_read(argument_address, 16))
                    key = tracked_guest_strings.get(key_handle)
                    value = tracked_guest_strings.get(value_handle)
                    if (key is None or key["label"] != "ApplicationUtilBase store key"
                            or issued_jni_local_refs.get(key_handle, 0) < 1
                            or value is None
                            or value["label"] != "ApplicationUtilBase store value"
                            or issued_jni_local_refs.get(value_handle, 0) < 1):
                        raise ValueError("storeKeyValue requires two tracked local Strings")
                    guest_preferences[key["value"]] = value["value"]
                    call_record.update({"class": class_name,
                                        "method_name": "storeKeyValue",
                                        "key": key["value"], "value": value["value"],
                                        "basis": "APK stores the value in both SharedPreferences wrappers",
                                        "apk_bytecode_source": (
                                            ".local-runtime/build-20260923-224545/decoded/"
                                            "smali/com/supercell/titan/ApplicationUtilBase.smali:1804")})
                    result = arg0  # Void JNI call.
                elif not is_vm and index == 118:
                    if (arg0 != env_for_thread or arg1 not in class_by_handle
                            or class_by_handle[arg1]["name"] != "com/supercell/titan/GameApp"
                            or arg2 != JNI_EMULATOR_METHOD_ID
                            or arg2 not in issued_static_method_ids
                            or emulator_bytecode_source is None):
                        raise ValueError("only tracked APK-verified GameApp.isEmulator()Z invocation is supported")
                    call_record.update({"class": "com/supercell/titan/GameApp",
                                        "method_name": "isEmulator", "signature": "()Z",
                                        "va_list": hex(arg3),
                                        "apk_bytecode_source": emulator_bytecode_source,
                                        "return_basis": "const/4 v0, 0x0; return v0"})
                    result = 0
                elif not is_vm and index in (164, 165, 166):
                    guest_string = tracked_guest_strings.get(arg1)
                    if arg0 != env_for_thread or guest_string is None:
                        raise ValueError("JNI string API requires the tracked environment and virtual String")
                    utf16 = guest_string["value"].encode("utf-16-le")
                    call_record.update({"string_handle": hex(arg1),
                                        "tracked_string": guest_string["label"],
                                        "utf16_code_units": len(utf16) // 2,
                                        "source": "explicit guest virtual path or pinned literal"})
                    if index == 164:
                        result = len(utf16) // 2
                    elif index == 165:
                        if arg2:
                            if not any(begin <= arg2 and arg2 <= end and permissions & UC_PROT_WRITE
                                       for begin, end, permissions in guest.mem_regions()):
                                raise ValueError("GetStringChars isCopy pointer is not guest-writable")
                        char_buffer = allocate(len(utf16) + 2, alignment=2)
                        if char_buffer == 0:
                            raise ValueError("guest heap exhausted for GetStringChars")
                        guest.mem_write(char_buffer, utf16 + b"\0\0")
                        if arg2:
                            guest.mem_write(arg2, b"\x01")  # JNI_TRUE: a diagnostic copy.
                        active_string_chars[char_buffer] = {
                            "string_handle": arg1, "label": guest_string["label"],
                            "utf16_bytes": len(utf16)}
                        call_record.update({"is_copy_pointer": hex(arg2),
                                            "is_copy_written": bool(arg2),
                                            "utf16_buffer": hex(char_buffer),
                                            "utf16_le_hex": utf16.hex()})
                        result = char_buffer
                    else:
                        active = active_string_chars.get(arg2)
                        if active is None or active["string_handle"] != arg1:
                            raise ValueError("ReleaseStringChars requires the matching active guest copy")
                        call_record.update({"utf16_buffer": hex(arg2),
                                            "released_utf16_bytes": active["utf16_bytes"]})
                        del active_string_chars[arg2]
                        result = arg0  # Void JNI call: x0 is not a meaningful return value.
                elif not is_vm and index in (16, 17):
                    if arg0 != env_for_thread:
                        raise ValueError(f"{api} requires the tracked environment")
                    call_record["pending_exception"] = False
                    result = 0
                else:
                    stop_jni(guest, api, caller, "JNI vtable slot is not implemented")
                    return
            except (UcError, UnicodeDecodeError, ValueError) as error:
                stop_jni(guest, api, caller, str(error))
                return
            call_record["result"] = hex(result)
            guest.reg_write(UC_ARM64_REG_X0, result)
            guest.reg_write(UC_ARM64_REG_PC, guest.reg_read(UC_ARM64_REG_X30))

        jni_handle = machine.hook_add(UC_HOOK_CODE, on_jni_api,
                                      begin=JNI_ENV_STUBS, end=JNI_VM_STUBS + 8 * 16 - 1)
        try:
            machine.reg_write(UC_ARM64_REG_X1, 0)  # JNI_OnLoad's reserved argument.
            machine.reg_write(UC_ARM64_REG_X2, 0)
            try:
                version = call(machine, JNI_ONLOAD, JNI_VM, instruction_budget=5_000_000,
                               timeout_microseconds=60_000_000)
                returned = True
                error = None
            except (UcError, RuntimeError) as failure:
                version = None
                returned = False
                error = str(failure)
            canary_unchanged = (struct.unpack("<Q", machine.mem_read(JNI_TLS + 0x28, 8))[0]
                                == JNI_TLS_CANARY)
            jni_result = {"entry": hex(BASE + JNI_ONLOAD), "returned": returned,
                    "jni_version": hex(version) if version is not None else None,
                    "expected_jni_version": "0x10006", "error": error,
                    "program_counter": hex(machine.reg_read(UC_ARM64_REG_PC)),
                    "tls_canary_unchanged": canary_unchanged,
                    "java_vm": hex(JNI_VM), "jni_env": hex(JNI_ENV),
                    "classes": classes, "class_count": len(classes),
                    "method_count": sum(len(entry["methods"]) for entry in classes),
                    "jni_calls": jni_calls.copy(), "imports_seen": imports_seen[first_import:],
                    "memory_faults": faults[first_fault:],
                    "first_unhandled_call": (unhandled[first_unhandled]
                                             if len(unhandled) > first_unhandled else None),
                    "diagnostic_limit": "synthetic JNI registry does not initialize Java or battle state"}
            if diagnose_application_create:
                if not returned or version != 0x10006 or not canary_unchanged:
                    jni_result["native_libraries_loaded_diagnostic"] = {
                        "attempted": False, "reason": "JNI_OnLoad did not return JNI 1.6 with intact TLS canary"}
                    jni_result["application_create_diagnostic"] = {
                        "attempted": False,
                        "reason": "native libraries callback was not attempted"}
                else:
                    def attempt_registered_native(class_name: str, method_name: str,
                                                  signature: str = "()V",
                                                  extra_args: tuple[int, ...] = ()) -> dict:
                        native_class = class_by_name.get(class_name)
                        candidates = ([method for method in native_class["methods"]
                                       if method["name"] == method_name
                                       and method["signature"] == signature]
                                      if native_class else [])
                        if len(candidates) != 1:
                            return {"attempted": False, "reason":
                                    f"expected one registered {class_name}.{method_name} {signature}"}
                        if len(extra_args) > 3:
                            raise ValueError("diagnostic native callback supports at most three Java arguments")
                        target = int(candidates[0]["function_pointer"], 16)
                        class_handle = int(native_class["handle"], 16)
                        first_import = len(imports_seen)
                        first_fault = len(faults)
                        first_unhandled = len(unhandled)
                        first_jni_call = len(jni_calls)
                        first_allocation = len(allocations)
                        first_telemetry_call = len(telemetry_adapter_calls)
                        singleton_before = struct.unpack(
                            "<Q", machine.mem_read(BASE + APPLICATION_SINGLETON_SLOT, 8))[0]
                        machine.reg_write(UC_ARM64_REG_X1, class_handle)
                        for register, value in zip((UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                                    UC_ARM64_REG_X4),
                                                   (*extra_args, *([0] * (3 - len(extra_args))))):
                            machine.reg_write(register, value)
                        try:
                            # These registered Java lifecycle natives have void returns.
                            call(machine, target - BASE, JNI_ENV,
                                 instruction_budget=1_000_000, timeout_microseconds=20_000_000)
                            native_returned = True
                            native_error = None
                        except (UcError, RuntimeError) as failure:
                            native_returned = False
                            if len(unhandled) > first_unhandled:
                                native_error = ("stopped at unsupported call: "
                                                + unhandled[first_unhandled]["symbol"])
                            elif len(faults) > first_fault:
                                native_error = "stopped at guest memory fault"
                            else:
                                native_error = str(failure)
                        singleton_after = struct.unpack(
                            "<Q", machine.mem_read(BASE + APPLICATION_SINGLETON_SLOT, 8))[0]
                        return {"attempted": True, "entry": hex(target),
                                "source": f"registered {class_name}.{method_name} {signature}",
                                "environment": hex(JNI_ENV), "class_handle": hex(class_handle),
                                "instruction_budget": 1_000_000, "timeout_microseconds": 20_000_000,
                                "returned": native_returned, "error": native_error,
                                "program_counter": hex(machine.reg_read(UC_ARM64_REG_PC)),
                                "registers_x0_x3": [hex(machine.reg_read(register)) for register in
                                                    (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                                     UC_ARM64_REG_X2, UC_ARM64_REG_X3)],
                                "tls_canary_unchanged": (struct.unpack(
                                    "<Q", machine.mem_read(JNI_TLS + 0x28, 8))[0] == JNI_TLS_CANARY),
                                "application_singleton_slot": hex(BASE + APPLICATION_SINGLETON_SLOT),
                                "singleton_before": hex(singleton_before),
                                "singleton_after": hex(singleton_after),
                                "diagnostic_heap_allocations": len(allocations) - first_allocation,
                                "telemetry_adapter_calls": telemetry_adapter_calls[first_telemetry_call:],
                                "jni_calls": jni_calls[first_jni_call:],
                                "imports_seen": imports_seen[first_import:],
                                "memory_faults": faults[first_fault:],
                                "first_unhandled_call": (unhandled[first_unhandled]
                                                         if len(unhandled) > first_unhandled else None),
                                "diagnostic_limit": "native callback does not prove Android app or battle readiness"}

                    library_result = attempt_registered_native(
                        "com/supercell/titan/TitanApplication", "nOnNativeLibrariesLoaded")
                    jni_result["native_libraries_loaded_diagnostic"] = library_result
                    if library_result.get("returned") and library_result["tls_canary_unchanged"]:
                        jni_result["application_create_diagnostic"] = attempt_registered_native(
                            "com/supercell/titan/GameApp", "nOnApplicationCreate")
                    else:
                        jni_result["application_create_diagnostic"] = {
                            "attempted": False,
                            "reason": "native libraries callback did not return with intact TLS canary"}
            if diagnose_create_game_main:
                library_result = jni_result.get("native_libraries_loaded_diagnostic", {})
                application_result = jni_result.get("application_create_diagnostic", {})
                game_app = class_by_name.get("com/supercell/titan/GameApp")
                expected_signature = (
                    "(Landroid/content/res/AssetManager;Ljava/lang/String;Ljava/lang/String;"
                    "Ljava/lang/String;JIIIFFILjava/lang/String;Landroid/app/Activity;)Ljava/lang/String;")
                candidates = ([method for method in game_app["methods"]
                               if method["name"] == "createGameMain"
                               and method["signature"] == expected_signature] if game_app else [])
                if (not library_result.get("returned") or not application_result.get("returned")
                        or not library_result.get("tls_canary_unchanged")
                        or not application_result.get("tls_canary_unchanged")
                        or len(candidates) != 1):
                    jni_result["create_game_main_diagnostic"] = {
                        "attempted": False,
                        "reason": "both startup natives must return with intact TLS and one registered createGameMain ABI"}
                else:
                    target = int(candidates[0]["function_pointer"], 16)
                    class_handle = int(game_app["handle"], 16)
                    tracked_global_ref_handles.update({
                        JNI_OPAQUE_ACTIVITY: "Activity",
                        JNI_OPAQUE_ASSET_MANAGER: "AssetManager",
                    })
                    first_import = len(imports_seen)
                    first_fault = len(faults)
                    first_unhandled = len(unhandled)
                    first_jni_call = len(jni_calls)
                    first_allocation = len(allocations)
                    first_stdio_call = len(virtual_stdio_calls)
                    first_asset_bridge_call = len(asset_manager_bridge_calls)
                    root_before = struct.unpack("<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0]
                    flags_before = struct.unpack("<I", machine.mem_read(
                        BASE + APPLICATION_FLAGS_SLOT, 4))[0]
                    entry_sp = STACK + STACK_SIZE - 0x100
                    stack_args = (1920, 420, 2, JNI_OPAQUE_UNKNOWN, JNI_OPAQUE_ACTIVITY)
                    machine.mem_write(entry_sp, struct.pack("<5Q", *stack_args))
                    integer_args = (JNI_ENV, class_handle, JNI_OPAQUE_ASSET_MANAGER,
                                    JNI_OPAQUE_DATA_DIR, JNI_OPAQUE_CACHE_DIR,
                                    JNI_OPAQUE_CACHE_DIR, 1_073_741_824, 1080)
                    for register, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                                UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                                UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                                UC_ARM64_REG_X6, UC_ARM64_REG_X7), integer_args):
                        machine.reg_write(register, value)
                    dpi_bits = struct.unpack("<I", struct.pack("<f", 440.0))[0]
                    machine.reg_write(UC_ARM64_REG_S0, dpi_bits)
                    machine.reg_write(UC_ARM64_REG_S1, dpi_bits)
                    machine.reg_write(UC_ARM64_REG_SP, entry_sp)
                    machine.reg_write(UC_ARM64_REG_X30, RETURN)
                    try:
                        machine.emu_start(target, RETURN,
                                          timeout=20_000_000, count=1_000_000)
                        if machine.reg_read(UC_ARM64_REG_PC) != RETURN:
                            raise RuntimeError("guest createGameMain did not return within the diagnostic budget")
                        main_returned = True
                        main_error = None
                    except (UcError, RuntimeError) as failure:
                        main_returned = False
                        if len(unhandled) > first_unhandled:
                            main_error = ("stopped at unsupported call: "
                                          + unhandled[first_unhandled]["symbol"])
                        elif len(faults) > first_fault:
                            main_error = "stopped at guest memory fault"
                        else:
                            main_error = str(failure)
                    root_after = struct.unpack("<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0]
                    flags_after = struct.unpack("<I", machine.mem_read(
                        BASE + APPLICATION_FLAGS_SLOT, 4))[0]
                    jni_result["create_game_main_diagnostic"] = {
                        "attempted": True, "entry": hex(target),
                        "source": "registered GameApp.createGameMain",
                        "registered_signature": expected_signature,
                        "instruction_budget": 1_000_000, "timeout_microseconds": 20_000_000,
                        "input_registers_x0_x7": [hex(value) for value in integer_args],
                        "input_stack_pointer": hex(entry_sp),
                        "input_stack_slots_0_32": [hex(value) for value in stack_args],
                        "synthetic_dpi_x_y": [440.0, 440.0],
                        "opaque_handles": {"AssetManager": hex(JNI_OPAQUE_ASSET_MANAGER),
                                           "Activity": hex(JNI_OPAQUE_ACTIVITY),
                                           "dataDir_String": hex(JNI_OPAQUE_DATA_DIR),
                                           "cacheDir_String": hex(JNI_OPAQUE_CACHE_DIR),
                                           "unknown_String": hex(JNI_OPAQUE_UNKNOWN)},
                        "returned": main_returned, "error": main_error,
                        "program_counter": hex(machine.reg_read(UC_ARM64_REG_PC)),
                        "registers_x0_x3": [hex(machine.reg_read(register)) for register in
                                            (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                             UC_ARM64_REG_X2, UC_ARM64_REG_X3)],
                        "tls_canary_unchanged": (struct.unpack(
                            "<Q", machine.mem_read(JNI_TLS + 0x28, 8))[0] == JNI_TLS_CANARY),
                        "context_root_slot": hex(BASE + CONTEXT_ROOT_SLOT),
                        "context_root_before": hex(root_before),
                        "context_root_after": hex(root_after),
                        "application_flags_slot": hex(BASE + APPLICATION_FLAGS_SLOT),
                        "application_flags_before": hex(flags_before),
                        "application_flags_after": hex(flags_after),
                        "application_flag_0x200_set_by_guest": (
                            not bool(flags_before & 0x200) and bool(flags_after & 0x200)),
                        "virtual_cmdline_argv": [pinned_package],
                        "virtual_cmdline_content_hex": virtual_cmdline.hex(),
                        "virtual_cmdline_prefix_14_ascii": virtual_cmdline[:14].decode("ascii"),
                        "virtual_cmdline_file_final_state": virtual_file["state"],
                        "virtual_stdio_calls": virtual_stdio_calls[first_stdio_call:],
                        "asset_manager_bridge_calls": asset_manager_bridge_calls[
                            first_asset_bridge_call:],
                        "assets_directory_validation": assets_dir_metadata,
                        "diagnostic_heap_allocations": len(allocations) - first_allocation,
                        "jni_calls": jni_calls[first_jni_call:],
                        "imports_seen": imports_seen[first_import:],
                        "memory_faults": faults[first_fault:],
                        "first_unhandled_call": (unhandled[first_unhandled]
                                                 if len(unhandled) > first_unhandled else None),
                        "diagnostic_limit": "opaque Java handles and no native asset provider; no content or battle readiness claim"}
                    if main_returned and jni_result["create_game_main_diagnostic"]["tls_canary_unchanged"]:
                        on_create_root_before = struct.unpack(
                            "<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0]
                        on_create_result = attempt_registered_native(
                            "com/supercell/titan/GameApp", "nOnCreate")
                        on_create_result.update({
                            "lifecycle_order_basis": "APK GameApp.onCreate calls nOnCreate after createGameMain",
                            "java_view_setup_executed": False,
                            "context_root_before": hex(on_create_root_before),
                            "context_root_after": hex(struct.unpack(
                                "<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0]),
                            "queued_guest_workers": [{"handle": hex(handle),
                                                      "start_routine": hex(worker["start_routine"]),
                                                      "start_argument": hex(worker["start_argument"]),
                                                      "joinable": worker["joinable"]}
                                                     for handle, worker in guest_threads.items()],
                        })
                        jni_result["on_create_diagnostic"] = on_create_result
                        if on_create_result["returned"]:
                            jni_result["on_start_diagnostics"] = [
                                attempt_registered_native(
                                    "com/supercell/titan/GameApp", "nOnStart")]
                            if jni_result["on_start_diagnostics"][0].get("returned"):
                                jni_result["on_start_diagnostics"].append(
                                    attempt_registered_native(
                                        "com/supercell/titan/GameApp", "nOnStart"))
                            jni_result["on_start_diagnostic"] = jni_result["on_start_diagnostics"][0]
                            if all(call.get("returned") for call in
                                   jni_result["on_start_diagnostics"]) and len(
                                       jni_result["on_start_diagnostics"]) == 2:
                                jni_result["on_resume_diagnostic"] = attempt_registered_native(
                                    "com/supercell/titan/GameApp", "nOnResume")
                                if jni_result["on_resume_diagnostic"].get("returned"):
                                    jni_result["surface_created_diagnostic"] = (
                                        attempt_registered_native(
                                            "com/supercell/titan/GameApp", "nOnSurfaceCreated",
                                            "(Landroid/view/Surface;)V",
                                            (JNI_OPAQUE_SURFACE,)))
                                    if jni_result["surface_created_diagnostic"].get("returned"):
                                        jni_result["surface_changed_diagnostic"] = (
                                            attempt_registered_native(
                                                "com/supercell/titan/GameApp",
                                                "nOnSurfaceChanged",
                                                "(Landroid/view/Surface;II)V",
                                                (JNI_OPAQUE_SURFACE, 1080, 1920)))
                        worker = guest_threads.get(WORKER_HANDLE)
                        if (on_create_result["returned"]
                                and len(jni_result.get("on_start_diagnostics", [])) == 2
                                and all(call.get("returned") for call in
                                        jni_result["on_start_diagnostics"])
                                and jni_result.get("on_resume_diagnostic", {}).get("returned")
                                and jni_result.get("surface_created_diagnostic", {}).get("returned")
                                and jni_result.get("surface_changed_diagnostic", {}).get("returned")
                                and worker is not None):
                            def probe_thread_once(handle: int, budget: int) -> dict:
                                nonlocal active_guest_thread_id
                                thread = guest_threads[handle]
                                parent_context = machine.context_save()
                                first_import = len(imports_seen)
                                first_fault = len(faults)
                                first_unhandled = len(unhandled)
                                first_jni = len(jni_calls)
                                new_thread = thread["state"] == "queued"
                                if new_thread:
                                    machine.mem_map(thread["stack"], WORKER_STACK_SIZE)
                                    machine.mem_map(thread["tls"], PAGE)
                                    machine.mem_write(thread["tls"] + 0x28,
                                                      struct.pack("<Q", JNI_TLS_CANARY))
                                    machine.reg_write(UC_ARM64_REG_TPIDR_EL0, thread["tls"])
                                    machine.reg_write(UC_ARM64_REG_X0, thread["start_argument"])
                                    machine.reg_write(UC_ARM64_REG_SP,
                                                      thread["stack"] + WORKER_STACK_SIZE)
                                    machine.reg_write(UC_ARM64_REG_X30, RETURN)
                                    start_pc = thread["start_routine"]
                                elif thread["state"] == "runnable":
                                    machine.context_restore(thread["context"])
                                    start_pc = machine.reg_read(UC_ARM64_REG_PC)
                                elif thread["state"] == "runnable_after_wait":
                                    machine.context_restore(thread["context"])
                                    wait_mutex_pointer = thread["wait_mutex"]
                                    tracked_mutex = mutexes[wait_mutex_pointer]
                                    if (tracked_mutex.recursive or tracked_mutex.depth != 0
                                            or not tracked_mutex.host_lock.acquire(blocking=False)):
                                        machine.context_restore(parent_context)
                                        raise ValueError("signaled guest condition waiter cannot reacquire its mutex")
                                    tracked_mutex.owner_tid = thread["thread_id"]
                                    tracked_mutex.depth = 1
                                    mirror_mutex_state(machine, wait_mutex_pointer, tracked_mutex)
                                    machine.reg_write(UC_ARM64_REG_X0,
                                                      thread.pop("wait_return_value"))
                                    machine.reg_write(UC_ARM64_REG_PC, thread["wait_return_address"])
                                    start_pc = thread["wait_return_address"]
                                    thread.pop("wait_condition")
                                    thread.pop("wait_mutex")
                                    thread.pop("wait_return_address")
                                    thread.pop("wait_deadline_ns")
                                else:
                                    raise ValueError("guest thread is not runnable for a diagnostic slice")
                                thread["state"] = "running"
                                active_guest_thread_id = thread["thread_id"]
                                if angle_backend is not None and angle_state["context"]:
                                    guest_context = virtual_egl["current_context_by_thread"].get(
                                        active_guest_thread_id, 0)
                                    native_context = (angle_state["context"] if guest_context
                                                      == VIRTUAL_EGL_CONTEXT else 0)
                                    native_surface = angle_state["surface"] if native_context else 0
                                    if not angle_backend.make_current(
                                            angle_state["display"], native_surface,
                                            native_context):
                                        raise RuntimeError("ANGLE failed to restore guest thread context: "
                                                           + hex(angle_backend.get_error()))
                                try:
                                    machine.emu_start(start_pc, RETURN,
                                                      timeout=20_000_000, count=budget)
                                    returned = machine.reg_read(UC_ARM64_REG_PC) == RETURN
                                    error = (None if returned else
                                             "guest thread reached diagnostic instruction or time budget")
                                except UcError as failure:
                                    returned = False
                                    error = str(failure)
                                parked = thread["state"] == "waiting_condition"
                                if parked:
                                    error = None
                                elif len(unhandled) > first_unhandled:
                                    error = ("stopped at unsupported call: "
                                             + unhandled[first_unhandled]["symbol"])
                                elif len(faults) > first_fault:
                                    error = "stopped at guest memory fault"
                                thread["context"] = machine.context_save()
                                thread["state"] = ("waiting_condition" if parked else
                                                   "returned" if returned else
                                                   "stopped" if len(unhandled) > first_unhandled
                                                   or len(faults) > first_fault else "runnable")
                                slice_imports = imports_seen[first_import:]
                                slice_jni = jni_calls[first_jni:]
                                diagnostic = {
                                    "attempted": True,
                                    "entry_kind": "new" if new_thread else "resume",
                                    "slice_entry": hex(start_pc),
                                    "kind": thread["kind"],
                                    "start_routine": hex(thread["start_routine"]),
                                    "start_argument": hex(thread["start_argument"]),
                                    "callback": hex(thread["callback"]),
                                    "thread_handle": hex(handle),
                                    "guest_thread_id": thread["thread_id"],
                                    "joinable": thread["joinable"],
                                    "thread_tls": hex(thread["tls"]),
                                    "thread_stack": [hex(thread["stack"]),
                                                     hex(thread["stack"] + WORKER_STACK_SIZE)],
                                    "instruction_budget": budget,
                                    "returned": returned,
                                    "parked_on_condition": parked,
                                    "thread_state": thread["state"],
                                    "wait_condition": (hex(thread["wait_condition"])
                                                       if "wait_condition" in thread else None),
                                    "wait_deadline_wall_ns": thread.get("wait_deadline_ns"),
                                    "error": error,
                                    "program_counter": hex(machine.reg_read(UC_ARM64_REG_PC)),
                                    "context_root_after": hex(struct.unpack(
                                        "<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0]),
                                    "tls_canary_unchanged": (struct.unpack(
                                        "<Q", machine.mem_read(thread["tls"] + 0x28, 8))[0]
                                                             == JNI_TLS_CANARY),
                                    "imports_seen_count": len(slice_imports),
                                    "imports_seen": (slice_imports if len(slice_imports) <= 200
                                                     else slice_imports[:100] + slice_imports[-100:]),
                                    "imports_trace_truncated": len(slice_imports) > 200,
                                    "jni_calls_count": len(slice_jni),
                                    "jni_calls": (slice_jni if len(slice_jni) <= 200
                                                  else slice_jni[:100] + slice_jni[-100:]),
                                    "jni_trace_truncated": len(slice_jni) > 200,
                                    "memory_faults": faults[first_fault:],
                                    "first_unhandled_call": (unhandled[first_unhandled]
                                                             if len(unhandled) > first_unhandled
                                                             else None),
                                    "diagnostic_limit": "one guest thread slice; no completed scheduler or battle state claim",
                                }
                                del imports_seen[first_import:]
                                active_guest_thread_id = GUEST_THREAD_ID
                                machine.context_restore(parent_context)
                                return diagnostic

                            jni_result["worker_start_diagnostic"] = probe_thread_once(
                                WORKER_HANDLE, 1_000_000)
                            jni_result["pool_worker_start_diagnostics"] = [
                                probe_thread_once(handle, 200_000)
                                for handle, thread in list(guest_threads.items())
                                if thread["kind"] == "pool" and thread["state"] == "queued"]
                            if guest_threads[WORKER_HANDLE]["state"] == "runnable":
                                jni_result["worker_followup_diagnostic"] = probe_thread_once(
                                    WORKER_HANDLE, 5_000_000)
                            jni_result["timer_worker_start_diagnostics"] = [
                                probe_thread_once(handle, 200_000)
                                for handle, thread in list(guest_threads.items())
                                if thread["kind"] == "timer" and thread["state"] == "queued"]
                            scheduler_rounds = []
                            jni_result["bounded_scheduler_diagnostic"] = {
                                "round_limit": 800, "rounds": scheduler_rounds,
                                "stop_reason": None}
                            for round_index in range(800):
                                ready = [handle for handle, thread in guest_threads.items()
                                         if thread["state"] in ("queued", "runnable",
                                                                "runnable_after_wait")]
                                advance = None
                                if not ready:
                                    timed_waiters = [(thread["wait_deadline_ns"], handle)
                                                     for handle, thread in guest_threads.items()
                                                     if thread["state"] == "waiting_condition"
                                                     and thread.get("wait_deadline_ns") is not None]
                                    if not timed_waiters:
                                        jni_result["bounded_scheduler_diagnostic"]["stop_reason"] = (
                                            "no runnable or timed guest threads")
                                        break
                                    next_deadline = min(deadline for deadline, _handle in timed_waiters)
                                    elapsed_ns = max(0, next_deadline - virtual_clock["wall_ns"])
                                    virtual_clock["wall_ns"] += elapsed_ns
                                    virtual_clock["monotonic_ns"] += elapsed_ns
                                    expired_handles = []
                                    for deadline, handle in timed_waiters:
                                        if deadline > virtual_clock["wall_ns"]:
                                            continue
                                        waiting_thread = guest_threads[handle]
                                        condition_pointer = waiting_thread["wait_condition"]
                                        condition_wait_queue[condition_pointer].remove(handle)
                                        condition_waiters[condition_pointer] = len(
                                            condition_wait_queue[condition_pointer])
                                        waiting_thread["state"] = "runnable_after_wait"
                                        waiting_thread["wait_return_value"] = 110
                                        expired_handles.append(handle)
                                    advance = {"elapsed_ns": elapsed_ns,
                                               "virtual_wall_ns": virtual_clock["wall_ns"],
                                               "expired_thread_handles": [hex(handle)
                                                                          for handle in expired_handles]}
                                    ready = expired_handles
                                round_record = {"index": round_index, "time_advance": advance,
                                                "thread_slices": []}
                                scheduler_rounds.append(round_record)
                                stop_detail = None
                                milestone_detail = None
                                for handle in ready:
                                    thread = guest_threads[handle]
                                    if (thread["state"] == "runnable_after_wait"
                                            and mutexes[thread["wait_mutex"]].depth != 0):
                                        round_record["thread_slices"].append({
                                            "handle": hex(handle), "result": "waiting for mutex"})
                                        continue
                                    diagnostic = probe_thread_once(handle, 1_000_000)
                                    round_record["thread_slices"].append({
                                        "handle": hex(handle), "kind": diagnostic["kind"],
                                        "thread_state": diagnostic["thread_state"],
                                        "error": diagnostic["error"],
                                        "imports_seen_count": diagnostic["imports_seen_count"],
                                        "jni_calls_count": diagnostic["jni_calls_count"],
                                        "program_counter": diagnostic["program_counter"],
                                        "content_root_after": diagnostic["context_root_after"],
                                        "wait_condition": diagnostic["wait_condition"],
                                        "wait_deadline_wall_ns": diagnostic["wait_deadline_wall_ns"],
                                        "last_import": (diagnostic["imports_seen"][-1]
                                                        if diagnostic["imports_seen"] else None),
                                        "last_jni": (diagnostic["jni_calls"][-1]
                                                     if diagnostic["jni_calls"] else None),
                                    })
                                    if round_index == 0:
                                        jni_result.setdefault("first_scheduler_slices", []).append(diagnostic)
                                    if diagnostic["first_unhandled_call"] or diagnostic["memory_faults"]:
                                        stop_detail = diagnostic
                                        break
                                    if diagnostic["context_root_after"] != "0x0":
                                        milestone_detail = diagnostic
                                        break
                                if stop_detail is not None:
                                    jni_result["bounded_scheduler_diagnostic"]["stop_reason"] = (
                                        "guest call or memory fault")
                                    jni_result["bounded_scheduler_diagnostic"]["stop_detail"] = stop_detail
                                    break
                                if milestone_detail is not None:
                                    jni_result["bounded_scheduler_diagnostic"]["stop_reason"] = (
                                        "original content root became nonzero")
                                    jni_result["bounded_scheduler_diagnostic"]["stop_detail"] = milestone_detail
                                    break
                            else:
                                jni_result["bounded_scheduler_diagnostic"]["stop_reason"] = (
                                    "diagnostic round limit")
            jni_result["graphics_checkpoints"] = graphics_checkpoints
            jni_result["asset_manager_bridge_calls"] = asset_manager_bridge_calls
            jni_result["virtual_stdio_calls"] = virtual_stdio_calls
            jni_result["guest_update_output"] = {
                "state": virtual_update_output["state"],
                "bytes_written": virtual_update_output["bytes_written"],
                "content_sha256": virtual_update_output["digest"].hexdigest(),
            }
            return jni_result
        finally:
            machine.hook_del(jni_handle)

    try:
        if dependency_constructors is not None:
            dependency_result = attempt_prefix(dependency_constructors, len(dependency_constructors))
            if dependency_result["returned_prefix_count"] != len(dependency_constructors):
                return dependency_result, None, None
        else:
            dependency_result = None
        libg_result = attempt_prefix(constructors, libg_limit)
        if not diagnose_jni_onload:
            return dependency_result, libg_result, None
        if (dependency_result is None or len(constructors) != 753
                or libg_result["returned_prefix_count"] != len(constructors)):
            return dependency_result, libg_result, {"attempted": False,
                "reason": "JNI_OnLoad requires all 3 dependency and 753 libg constructors to return"}
        return dependency_result, libg_result, attempt_jni_onload()
    finally:
        for asset in virtual_assets.values():
            asset["stream"].close()
        if virtual_update_output["stream"] is not None:
            virtual_update_output["stream"].close()
        generated_path = virtual_update_output["path"]
        if (generated_path is not None
                and generated_path.is_relative_to(guest_fs_root)
                and generated_path.is_file()):
            generated_path.unlink()
        machine.hook_del(invalid_handle)
        machine.hook_del(import_handle)
        machine.hook_del(gl_import_handle)
        for handle in graphics_checkpoint_handles:
            machine.hook_del(handle)
        if cpp_handle is not None:
            machine.hook_del(cpp_handle)
        machine.hook_del(dl_handle)
        machine.hook_del(once_handle)
        machine.hook_del(bsearch_handle)
        machine.hook_del(mprotect_first_handle)
        machine.hook_del(mprotect_second_handle)
        machine.hook_del(text_write_handle)
        if angle_backend is not None:
            try:
                if angle_state["context"]:
                    angle_backend.make_current(angle_state["display"], 0, 0)
                if angle_state["surface"]:
                    angle_backend.destroy_surface(angle_state["display"], angle_state["surface"])
                if angle_state["context"]:
                    angle_backend.destroy_context(angle_state["display"], angle_state["context"])
                if angle_state["initialized"]:
                    angle_backend.terminate(angle_state["display"])
            finally:
                angle_backend.close()


def run(path: Path, inspect_constructors: bool = False, libcxx_path: Path | None = None,
        constructor_limit: int = 16, diagnose_jni_onload: bool = False,
        diagnose_application_create: bool = False,
        diagnose_create_game_main: bool = False,
        assets_dir: Path | None = None,
        angle_dll_dir: Path | None = None) -> dict:
    if assets_dir is not None and not diagnose_create_game_main:
        raise ValueError("--assets-dir is only valid with createGameMain diagnosis")
    if angle_dll_dir is not None and not diagnose_create_game_main:
        raise ValueError("--angle-dll-dir is only valid with createGameMain diagnosis")
    assets_dir_metadata = None
    if assets_dir is not None:
        host_directory = assets_dir.resolve(strict=True)
        if not host_directory.is_dir():
            raise ValueError("--assets-dir must be a directory")
        assets_scdb = host_directory / "assets.scdb"
        fingerprint_path = host_directory / "fingerprint.json"
        if (not assets_scdb.is_file() or assets_scdb.stat().st_size == 0
                or not fingerprint_path.is_file() or fingerprint_path.stat().st_size == 0):
            raise ValueError("--assets-dir must contain nonempty assets.scdb and fingerprint.json")
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        fingerprint_version = fingerprint.get("version")
        if not isinstance(fingerprint_version, str) or not fingerprint_version:
            raise ValueError("assets fingerprint.json has no version string")
        expected_version = json.loads(MANIFEST.read_text(encoding="utf-8"))["runtime_content_version"]
        version_matches = fingerprint_version == expected_version
        assets_dir_metadata = {
            "host_directory": str(host_directory),
            "assets_scdb_bytes": assets_scdb.stat().st_size,
            "fingerprint_json_bytes": fingerprint_path.stat().st_size,
            "fingerprint_version": fingerprint_version,
            "expected_runtime_content_version": expected_version,
            "version_matches_expected": version_matches,
            "version_mismatch": None if version_matches else {
                "found": fingerprint_version, "expected": expected_version},
            "bridge_scope": "bounded extracted asset open/read/seek/length/close; direct file descriptors require verified compressed APK fallback",
        }
    machine, result, constructors, symbol_for_stub, cpp_constructors, loaded_objects = load_relative_image(
        path, libcxx_path)
    if assets_dir_metadata is not None:
        result["assets_directory_validation"] = assets_dir_metadata
    if diagnose_jni_onload and (not inspect_constructors or libcxx_path is None):
        raise ValueError("JNI_OnLoad diagnosis requires constructor diagnosis and local libc++_shared.so")
    if diagnose_application_create and not diagnose_jni_onload:
        raise ValueError("application-create diagnosis requires JNI_OnLoad diagnosis")
    if diagnose_create_game_main and not diagnose_application_create:
        raise ValueError("createGameMain diagnosis requires application-create diagnosis")
    if inspect_constructors:
        real_cpp_mutex_address = (int(result["local_library"]["libg_mutex_import_address"], 16)
                                  if "local_library" in result else None)
        dependency_result, libg_result, jni_result = diagnose_constructor_prefix(
            machine, constructors, symbol_for_stub, real_cpp_mutex_address,
            cpp_constructors if libcxx_path is not None else None,
            max(constructor_limit, len(constructors)) if diagnose_jni_onload else constructor_limit,
            loaded_objects, diagnose_jni_onload, diagnose_application_create,
            diagnose_create_game_main, assets_dir_metadata, angle_dll_dir)
        if dependency_result is not None:
            result["local_library"]["constructor_prefix_diagnostic"] = dependency_result
            result["local_library"]["diagnostic_constructors_returned"] = dependency_result["returned_prefix_count"]
        if libg_result is None:
            result["libg_constructor_diagnostic_skipped_reason"] = (
                "libc++_shared.so constructor prefix stopped before all dependency constructors returned")
        else:
            result["constructor_prefix_diagnostic"] = libg_result
            result["diagnostic_constructors_returned"] = libg_result["returned_prefix_count"]
        if diagnose_jni_onload:
            result["jni_onload_diagnostic"] = jni_result if jni_result is not None else {
                "attempted": False, "reason": "dependency constructors stopped before JNI_OnLoad"}
            result["native_content_root_after_diagnostic"] = hex(struct.unpack(
                "<Q", machine.mem_read(BASE + CONTEXT_ROOT_SLOT, 8))[0])
    manager = DATA + 0x100
    root = DATA + 0x300
    context = DATA + 0x500
    machine.mem_write(manager + 0x60, struct.pack("<I", 91))
    machine.mem_write(root + 0x20, struct.pack("<Q", context))
    machine.mem_write(BASE + CONTEXT_ROOT_SLOT, struct.pack("<Q", root))

    tick = call(machine, GAME_STATE_TICK, manager)
    context_a = call(machine, GAME_STATE_CONTEXT_A)
    context_b = call(machine, GAME_STATE_CONTEXT_B)
    if tick != 91 or context_a != context or context_b != BASE + 0x018EEDB0:
        raise RuntimeError("the original ARM64 functions returned unexpected values")
    result["calls"] = {
        "GameStateTick": {"offset": hex(GAME_STATE_TICK), "returned_tick": tick},
        "GameStateContextA": {"offset": hex(GAME_STATE_CONTEXT_A), "returned_synthetic_context": context_a == context},
        "GameStateContextB": {"offset": hex(GAME_STATE_CONTEXT_B), "returned_expected_address": context_b == BASE + 0x018EEDB0},
    }
    result["battle_initialized"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libg", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--diagnose-constructors", action="store_true")
    parser.add_argument("--constructor-limit", type=int, default=16,
                        help="maximum libg constructor prefix to diagnose (default: 16)")
    parser.add_argument("--libcxx", type=Path, help="local APK libc++_shared.so symbol provider")
    parser.add_argument("--diagnose-jni-onload", action="store_true",
                        help="after all constructors, trace bounded guest JNI_OnLoad native registration")
    parser.add_argument("--diagnose-application-create", action="store_true",
                        help="after JNI_OnLoad, call registered TitanApplication then GameApp startup natives")
    parser.add_argument("--diagnose-create-game-main", action="store_true",
                        help="after startup natives, trace registered createGameMain to first unsupported dependency")
    parser.add_argument("--assets-dir", type=Path,
                        help="validated local assets.scdb/fingerprint.json bridge for createGameMain diagnosis")
    parser.add_argument("--angle-dll-dir", type=Path,
                        help="local x64 ANGLE DLL pair for real offscreen EGL/GLES diagnosis")
    args = parser.parse_args()
    if args.constructor_limit < 1:
        parser.error("--constructor-limit must be positive")
    if args.diagnose_jni_onload and (not args.diagnose_constructors or args.libcxx is None):
        parser.error("--diagnose-jni-onload requires --diagnose-constructors and --libcxx")
    if args.diagnose_application_create and not args.diagnose_jni_onload:
        parser.error("--diagnose-application-create requires --diagnose-jni-onload")
    if args.diagnose_create_game_main and not args.diagnose_application_create:
        parser.error("--diagnose-create-game-main requires --diagnose-application-create")
    if args.assets_dir is not None and not args.diagnose_create_game_main:
        parser.error("--assets-dir requires --diagnose-create-game-main")
    if args.angle_dll_dir is not None and not args.diagnose_create_game_main:
        parser.error("--angle-dll-dir requires --diagnose-create-game-main")
    result = run(args.libg, args.diagnose_constructors, args.libcxx,
                 args.constructor_limit, args.diagnose_jni_onload,
                 args.diagnose_application_create, args.diagnose_create_game_main,
                 args.assets_dir, args.angle_dll_dir)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    def brief_diagnostic(diagnostic: dict) -> dict:
        brief = {
            key: value for key, value in diagnostic.items()
            if key not in ("imports_seen", "mutex_adapter_calls", "syscall_adapter_calls",
                           "condition_adapter_calls", "locale_adapter_calls",
                           "telemetry_adapter_calls",
                           "phdr_iteration_calls", "mprotect_calls", "patched_instructions")
        }
        brief["imports_seen_count"] = len(diagnostic["imports_seen"])
        brief["last_imports"] = diagnostic["imports_seen"][-5:]
        brief["mutex_adapter_calls_count"] = len(diagnostic["mutex_adapter_calls"])
        brief["last_mutex_adapter_calls"] = diagnostic["mutex_adapter_calls"][-5:]
        brief["syscall_adapter_calls_count"] = len(diagnostic["syscall_adapter_calls"])
        brief["last_syscall_adapter_calls"] = diagnostic["syscall_adapter_calls"][-5:]
        brief["condition_adapter_calls_count"] = len(diagnostic["condition_adapter_calls"])
        brief["last_condition_adapter_calls"] = diagnostic["condition_adapter_calls"][-5:]
        brief["locale_adapter_calls_count"] = len(diagnostic["locale_adapter_calls"])
        brief["last_locale_adapter_calls"] = diagnostic["locale_adapter_calls"][-5:]
        brief["telemetry_adapter_calls_count"] = len(diagnostic["telemetry_adapter_calls"])
        brief["last_telemetry_adapter_calls"] = diagnostic["telemetry_adapter_calls"][-5:]
        brief["phdr_iteration_calls_count"] = len(diagnostic["phdr_iteration_calls"])
        brief["last_phdr_iteration_calls"] = [
            {"visited": call["visited"], "mprotect_stage": call["mprotect_stage"],
             "patches_count": len(call["patches"])}
            for call in diagnostic["phdr_iteration_calls"][-2:]
        ]
        brief["mprotect_calls_count"] = len(diagnostic["mprotect_calls"])
        brief["last_mprotect_calls"] = diagnostic["mprotect_calls"][-2:]
        brief["patched_instructions_count"] = len(diagnostic["patched_instructions"])
        sorted_patch_offsets = sorted(
            int(item["address"], 16) - BASE for item in diagnostic["patched_instructions"])
        offset_lines = "".join(f"{offset:08x}\n" for offset in sorted_patch_offsets)
        brief["patched_offsets_sorted_lines_sha256"] = hashlib.sha256(
            offset_lines.encode("ascii")).hexdigest()
        brief["last_patched_instructions"] = diagnostic["patched_instructions"][-5:]
        return brief

    summary = {key: value for key, value in result.items() if key != "constructor_prefix_diagnostic"}
    if "constructor_prefix_diagnostic" in result:
        summary["constructor_prefix_diagnostic"] = brief_diagnostic(result["constructor_prefix_diagnostic"])
    if "local_library" in summary and "constructor_prefix_diagnostic" in summary["local_library"]:
        summary["local_library"] = dict(summary["local_library"])
        summary["local_library"]["constructor_prefix_diagnostic"] = brief_diagnostic(
            summary["local_library"]["constructor_prefix_diagnostic"])
    if "jni_onload_diagnostic" in summary:
        jni = summary["jni_onload_diagnostic"]
        summary["jni_onload_diagnostic"] = {
            key: value for key, value in jni.items()
            if key not in ("classes", "jni_calls", "imports_seen", "memory_faults",
                           "native_libraries_loaded_diagnostic", "application_create_diagnostic",
                           "create_game_main_diagnostic", "on_create_diagnostic",
                           "worker_start_diagnostic", "pool_worker_start_diagnostics",
                           "worker_followup_diagnostic")}
        summary["jni_onload_diagnostic"]["class_names"] = [
            item["name"] for item in jni.get("classes", [])]
        summary["jni_onload_diagnostic"]["jni_calls_count"] = len(jni.get("jni_calls", []))
        summary["jni_onload_diagnostic"]["last_jni_calls"] = jni.get("jni_calls", [])[-5:]
        summary["jni_onload_diagnostic"]["imports_seen_count"] = len(jni.get("imports_seen", []))
        summary["jni_onload_diagnostic"]["memory_faults_count"] = len(jni.get("memory_faults", []))
        for phase in ("native_libraries_loaded_diagnostic", "application_create_diagnostic",
                      "create_game_main_diagnostic", "on_create_diagnostic",
                      "worker_start_diagnostic", "worker_followup_diagnostic"):
            if phase in jni:
                native = jni[phase]
                summary["jni_onload_diagnostic"][phase] = {
                    key: value for key, value in native.items()
                    if key not in ("jni_calls", "imports_seen", "memory_faults",
                                   "telemetry_adapter_calls")}
                brief_native = summary["jni_onload_diagnostic"][phase]
                brief_native["jni_calls_count"] = native.get(
                    "jni_calls_count", len(native.get("jni_calls", [])))
                brief_native["last_jni_calls"] = native.get("jni_calls", [])[-5:]
                brief_native["imports_seen_count"] = native.get(
                    "imports_seen_count", len(native.get("imports_seen", [])))
                brief_native["last_imports"] = native.get("imports_seen", [])[-5:]
                brief_native["memory_faults_count"] = len(native.get("memory_faults", []))
                brief_native["telemetry_adapter_calls_count"] = len(
                    native.get("telemetry_adapter_calls", []))
                brief_native["last_telemetry_adapter_calls"] = native.get(
                    "telemetry_adapter_calls", [])[-5:]
        if "pool_worker_start_diagnostics" in jni:
            summary["jni_onload_diagnostic"]["pool_worker_start_diagnostics"] = [
                {**{key: value for key, value in native.items()
                    if key not in ("jni_calls", "imports_seen", "memory_faults")},
                 "last_imports": native.get("imports_seen", [])[-5:],
                 "last_jni_calls": native.get("jni_calls", [])[-5:]}
                for native in jni["pool_worker_start_diagnostics"]]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
