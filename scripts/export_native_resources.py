"""Export only locally authorized game resources; excludes accounts and saves."""
import argparse
import json
from pathlib import Path
import zipfile

from bridge.install_runtime import validate_apk, MANIFEST, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
