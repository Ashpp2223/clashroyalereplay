"""Export only locally authorized game resources; excludes accounts and saves."""
import argparse
import json
from pathlib import Path
import zipfile


def export_ability_icons(pack: Path, update: Path | None, output: Path, manifest: Path):
    """Decode named game sprites; never crop screenshots or alter engine assets.

    Decoder: https://github.com/obus-globus/sc5-parser
    Pinned source: 9108080256a0df6cd772d81afdbc26047d79d78d (MIT).
    """
    import hashlib
    import lzma
    import struct
    import tempfile
    import tomllib
    import zstandard
    from sc5_parser.parser import SC5File
    from sc5_parser.sctx import decode_sctx
    from sc5_parser._schemas.sc.flash.SC2.FileDescriptor import FileDescriptor
    from sc5_parser._schemas.sc.flash.SC2.ExportNames import ExportNames

    with zipfile.ZipFile(pack) as archive, tempfile.TemporaryDirectory(prefix="cr-ability-icons-") as temporary:
        files = {name.removeprefix("assets/"): name for name in archive.namelist() if name.startswith("assets/")}
        def read(name):
            overlay = update / name if update else None
            return overlay.read_bytes() if overlay and overlay.is_file() else archive.read(files[name])
        tables = {name for name in files if name.startswith("csv_logic/") and name.endswith(".toml")}
        if update:
            tables.update(path.relative_to(update).as_posix() for path in (update/"csv_logic").rglob("*.toml"))
        definitions = {}
        for name in sorted(tables):
            data = read(name)
            try:
                decoded = lzma.decompress(data[:9] + b"\0"*4 + data[9:], format=lzma.FORMAT_ALONE)
                document = tomllib.loads(decoded.decode("utf-8-sig"))
            except (lzma.LZMAError, UnicodeError, tomllib.TOMLDecodeError):
                continue
            for ability, definition in document.get("ABILITY", {}).items():
                if definition.get("IconSWF") and definition.get("IconExportName"):
                    definitions[ability] = definition
        root = Path(__file__).resolve().parent.parent
        cards = json.loads((root/"src/data/nativeCards.json").read_text(encoding="utf-8"))
        cards += json.loads((root/"src/data/cnEngineProfile.json").read_text(encoding="utf-8"))["catalog"]["cards"]
        required = {name for card in cards for name in card["abilityNames"]}
        missing = required - definitions.keys()
        if missing:
            raise ValueError(f"Missing game ability definitions: {sorted(missing)}")
        output.mkdir(parents=True, exist_ok=True)
        assets, result, decoded_files = {}, {}, {}
        for ability in sorted(required):
            definition = definitions[ability]
            name, export = definition["IconSWF"], definition["IconExportName"]
            if name not in decoded_files:
                data = read(name)
                local = Path(temporary)/Path(name).name
                local.write_bytes(data)
                assets[name] = hashlib.sha256(data).hexdigest()
                sc = SC5File(local)
                # This game's refs index the string table directly (slot 0 is
                # empty). The upstream parser subtracts one and mislabels icons.
                descriptor_size = struct.unpack_from("<I", data, 6)[0]
                descriptor = FileDescriptor.GetRootAs(data[10:10+descriptor_size], 0)
                inner = zstandard.ZstdDecompressor().decompress(data[10+descriptor_size:], max_output_size=100*1024*1024)
                position = descriptor.ResourcesOffset()
                size = struct.unpack_from("<I", inner, position)[0]
                exports = ExportNames.GetRootAs(inner[position+4:position+4+size], 0)
                sc.exports = {sc.strings[exports.NameRefIds(i)]: exports.ObjectIds(i) for i in range(exports.ObjectIdsLength())}
                textures = []
                for texture in sc.textures:
                    external = texture["external"]
                    if not external:
                        raise ValueError(f"Expected external game texture: {name}")
                    texture_name = f"sc/{external}"
                    data = read(texture_name)
                    texture_file = Path(temporary)/external
                    texture_file.write_bytes(data)
                    assets[texture_name] = hashlib.sha256(data).hexdigest()
                    textures.append(decode_sctx(str(texture_file)))
                decoded_files[name] = sc, textures
            sc, textures = decoded_files[name]
            image = sc.extract_sprite(export, textures)
            if image is None or not image.getbbox():
                raise ValueError(f"Empty game sprite: {name}#{export}")
            image.save(output/f"{export}.png")
            result[ability] = {"image": f"/abilities/{export}.png", "cost": definition.get("ManaCost", 0)}
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"abilities": result, "source": {
            "decoder": "https://github.com/obus-globus/sc5-parser/tree/9108080256a0df6cd772d81afdbc26047d79d78d",
            "assetsSha256": assets}}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(f"Exported {len(result)} game ability icons to {output}")

from bridge.install_runtime import validate_apk, MANIFEST, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--ability-pack", type=Path, help="Export game skill icons from an existing local resource pack")
    parser.add_argument("--ability-update", type=Path, help="Matching game update directory, applied over base assets")
    parser.add_argument("--ability-manifest", type=Path, default=Path("src/data/nativeAbilityIcons.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.ability_pack:
        export_ability_icons(args.ability_pack, args.ability_update, args.output, args.ability_manifest)
        return
    if not args.apk or not args.export:
        parser.error("--apk and --export are required unless exporting --ability-pack")
    validate_apk(args.apk)
    data = args.export / MANIFEST["package"] / "update"
    hashes = {file.relative_to(data).as_posix(): sha256(file) for file in data.rglob("*") if file.is_file()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        archive.writestr("manifest.json", json.dumps({"schema": "cr-native-resources.v1"}))
        archive.write(args.apk, "prepared.apk")
        archive.write(args.apk.with_suffix(".build.json"), "prepared.build.json")
        archive.writestr("export/export-receipt.json", json.dumps({"updateHashes": hashes}))
        for name in hashes:
            archive.write(data / name, "export/" + MANIFEST["package"] + "/update/" + name)
    print(str(args.output))


if __name__ == "__main__":
    main()
