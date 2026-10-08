"""Inventory the pinned ARM64 game library before a Windows host experiment.

This reads user-local game files. It writes metadata only, never copies binaries.
Requires ``lief`` in the Python interpreter used to run the script.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import lief


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "vendor/clash-royale-battle-engine/vendor/firstlight/supported_engine.json"
PROBE = ROOT / "vendor/clash-royale-battle-engine/vendor/firstlight/probe/cr_replay_probe.cpp"
OFFSET_RE = re.compile(r"constexpr\s+std::uintptr_t\s+(\w+Offset)\s*=\s*(0x[0-9a-fA-F]+)\s*;")
PROLOGUE_RE = re.compile(r"constexpr\s+std::uint8_t\s+(\w+Prologue)\[16\]\s*=\s*\{([^}]+)\}", re.S)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}


def file_bytes_at_virtual_address(binary: lief.ELF.Binary, path: Path, address: int, count: int) -> bytes | None:
    for segment in binary.segments:
        if segment.type != lief.ELF.SEGMENT_TYPES.LOAD:
            continue
        start = segment.virtual_address
        end = start + segment.physical_size
        if start <= address and address + count <= end:
            with path.open("rb") as source:
                source.seek(segment.file_offset + address - start)
                return source.read(count)
    return None


def entry_value(binary: lief.ELF.Binary, tag: lief.ELF.DYNAMIC_TAGS) -> int:
    return next((entry.value for entry in binary.dynamic_entries if entry.tag == tag), 0)


def inspect(libg: Path, assets: Path, fingerprint: Path | None = None) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    game = file_record(libg)
    if game["sha256"] != manifest["libg_sha256"]:
        raise ValueError("libg.so SHA-256 does not match the pinned engine manifest")
    binary = lief.parse(libg.read_bytes())
    if not isinstance(binary, lief.ELF.Binary) or binary.header.machine_type != lief.ELF.ARCH.AARCH64:
        raise ValueError("expected an AArch64 ELF library")

    probe_text = PROBE.read_text(encoding="utf-8")
    offsets = {name: int(value, 16) for name, value in OFFSET_RE.findall(probe_text)}
    prologues = {
        name.removesuffix("Prologue"): bytes(int(item.strip(), 16) for item in body.split(",") if item.strip())
        for name, body in PROLOGUE_RE.findall(probe_text)
    }
    offset_report = {}
    for name, address in sorted(offsets.items()):
        prefix = name.removesuffix("Offset")
        expected = prologues.get(prefix)
        actual = file_bytes_at_virtual_address(binary, libg, address, len(expected) if expected else 4)
        offset_report[name] = {
            "virtual_address": hex(address),
            "file_backed": actual is not None,
            "first_bytes": actual.hex() if actual else None,
            "prologue_matches": actual == expected if expected else None,
        }

    library_records = {
        path.name: file_record(path)
        for path in sorted(libg.parent.glob("*.so"))
        if path.name != libg.name
    }
    local_exporters: dict[str, list[str]] = {}
    local_dependencies: dict[str, list[str]] = {}
    for name in binary.libraries:
        candidate = libg.parent / name
        if not candidate.is_file():
            continue
        dependency = lief.parse(candidate.read_bytes())
        if not isinstance(dependency, lief.ELF.Binary):
            raise ValueError(f"cannot parse local dependency: {name}")
        local_dependencies[name] = dependency.libraries
        for symbol in dependency.dynamic_symbols:
            if symbol.shndx != 0 and symbol.name:
                local_exporters.setdefault(symbol.name, []).append(name)
    notes = {
        note.name + ":" + str(note.type).split(".")[-1]: bytes(note.description).hex()
        for note in binary.notes
    }
    undefined = sorted({symbol.name for symbol in binary.dynamic_symbols if symbol.shndx == 0 and symbol.name})
    content = None
    if fingerprint is not None and fingerprint.is_file():
        data = json.loads(fingerprint.read_text(encoding="utf-8"))
        content = {
            "fingerprint_file": file_record(fingerprint),
            "version": data.get("version"),
            "declared_sha": data.get("sha"),
            "matches_pinned_runtime_version": data.get("version") == manifest["runtime_content_version"],
        }
    result = {
        "schema": "cr-windows-native-inventory.v1",
        "engine_manifest": manifest,
        "game": game,
        "assets_scdb": file_record(assets),
        "content_fingerprint": content,
        "local_libraries": library_records,
        "elf": {
            "machine": str(binary.header.machine_type),
            "class": str(binary.header.identity_class),
            "entrypoint": hex(binary.entrypoint),
            "build_id_and_notes": notes,
            "needed": binary.libraries,
            "needed_not_in_game_directory": sorted(set(binary.libraries) - set(library_records)),
            "dynamic_symbol_count": len(binary.dynamic_symbols),
            "undefined_symbol_count": len(undefined),
            "undefined_symbols": undefined,
            "imports_provided_by_local_libraries": {
                name: sorted(local_exporters[name]) for name in undefined if name in local_exporters
            },
            "imports_without_local_provider": [name for name in undefined if name not in local_exporters],
            "local_library_dependencies": local_dependencies,
            "relocation_count": len(binary.relocations),
            "relocation_types": dict(sorted(Counter(str(item.type) for item in binary.relocations).items())),
            "init_array_bytes": entry_value(binary, lief.ELF.DYNAMIC_TAGS.INIT_ARRAYSZ),
            "init_array_entries": entry_value(binary, lief.ELF.DYNAMIC_TAGS.INIT_ARRAYSZ) // 8,
        },
        "probe_offsets": offset_report,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libg", type=Path, required=True)
    parser.add_argument("--assets-scdb", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, help="defaults to fingerprint.json beside assets.scdb")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.libg, args.assets_scdb, args.fingerprint or args.assets_scdb.with_name("fingerprint.json"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elf = result["elf"]
    mismatch = [name for name, value in result["probe_offsets"].items() if value["prologue_matches"] is False]
    print(json.dumps({
        "output": str(args.output.resolve()),
        "libg_sha256": result["game"]["sha256"],
        "needed": len(elf["needed"]),
        "undefined_symbols": elf["undefined_symbol_count"],
        "imports_without_local_provider": len(elf["imports_without_local_provider"]),
        "relocations": elf["relocation_count"],
        "init_array_entries": elf["init_array_entries"],
        "content_version": result["content_fingerprint"]["version"] if result["content_fingerprint"] else None,
        "content_version_matches": result["content_fingerprint"]["matches_pinned_runtime_version"] if result["content_fingerprint"] else None,
        "probe_prologue_mismatches": mismatch,
    }, ensure_ascii=False))
    if mismatch:
        raise SystemExit("probe prologue check failed")


if __name__ == "__main__":
    main()
