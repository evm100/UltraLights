#!/usr/bin/env python3
"""Turn a firmware build into a package: a .zip with manifest.json plus the images it lists.

The manifest follows contracts/schemas/firmware-manifest.schema.json and is what the
node builder (Web Serial), tools/flash.py and OTA use.

    firmware/tools/package.py c3-lights            -> firmware/build/packages/c3-lights-<ver>.zip
    firmware/tools/package.py c6-lights -o out.zip
"""
import argparse
import datetime
import hashlib
import json
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
ROOT = FW.parent
SCHEMA_DIR = ROOT / "contracts" / "schemas"

ROLE_BY_KEY = {"bootloader": "bootloader", "partition-table": "partition_table", "otadata": "otadata", "app": "app"}
NAME_BY_ROLE = {"bootloader": "bootloader.bin", "partition_table": "partition-table.bin",
                "otadata": "ota_data_initial.bin", "app": "ultralights.bin"}


def partitions(table: bytes):
    """Yield (label, type, subtype, offset, size) from a binary partition table."""
    for i in range(0, len(table), 32):
        entry = table[i:i + 32]
        if len(entry) < 32 or entry[:2] != b"\xaa\x50":
            break
        _, ptype, sub, off, size, label, _ = struct.unpack("<2sBBII16sI", entry)
        yield label.rstrip(b"\0").decode(), ptype, sub, off, size


def git_commit() -> str:
    try:
        rev = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", "firmware", "contracts"],
                                        text=True).strip()
        return rev + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "0000000-dirty"


def validate(manifest: dict) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource
    except ImportError:
        print("note: jsonschema not installed, manifest not schema-checked", file=sys.stderr)
        return
    schemas = [json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")]
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    schema = next(s for s in schemas if s["$id"].endswith("/firmware-manifest.schema.json"))
    errors = list(Draft202012Validator(schema, registry=reg, format_checker=FormatChecker()).iter_errors(manifest))
    if errors:
        sys.exit(f"manifest invalid: {errors[0].message}")


def build_manifest(build: Path, profile: str) -> tuple[dict, dict[str, Path]]:
    flasher = json.loads((build / "flasher_args.json").read_text())
    desc = json.loads((build / "project_description.json").read_text())
    files, sources = [], {}
    for key, role in ROLE_BY_KEY.items():
        entry = flasher[key]
        src = build / entry["file"]
        data = src.read_bytes()
        name = NAME_BY_ROLE[role]
        files.append({"name": name, "offset": int(entry["offset"], 16), "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "role": role})
        sources[name] = src
    table = (build / flasher["partition-table"]["file"]).read_bytes()
    board = next(((off, size) for label, _t, _s, off, size in partitions(table) if label == "ul_board"), None)
    if not board:
        sys.exit("partition table has no ul_board partition")
    fs = flasher["flash_settings"]
    manifest = {
        "v": 1,
        "profile": profile,
        "chip": desc["target"],
        "version": desc["project_version"],
        "idf": desc.get("git_revision", "unknown"),
        "commit": git_commit(),
        "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "flash": {"size": fs["flash_size"], "mode": fs["flash_mode"], "freq": fs["flash_freq"]},
        "files": sorted(files, key=lambda f: f["offset"]),
        "app": NAME_BY_ROLE["app"],
        "board_partition": {"offset": board[0], "size": board[1]},
    }
    return manifest, sources


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", help="profile name, e.g. c3-lights (build dir firmware/build/<profile>)")
    ap.add_argument("--build-dir", type=Path, help="override the build directory")
    ap.add_argument("-o", "--output", type=Path, help="output .zip")
    args = ap.parse_args()
    build = args.build_dir or FW / "build" / args.profile
    if not (build / "flasher_args.json").exists():
        sys.exit(f"{build} has no build; run firmware/tools/build.sh {args.profile}")
    manifest, sources = build_manifest(build, args.profile)
    if not manifest["chip"] or not args.profile.startswith(manifest["chip"][-2:]):
        sys.exit(f"profile {args.profile} doesn't match the build target {manifest['chip']}")
    validate(manifest)
    out = args.output or FW / "build" / "packages" / f"{args.profile}-{manifest['version']}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for name, src in sources.items():
            z.write(src, name)
    print(f"{out}  ({manifest['chip']} {manifest['version']} {manifest['commit']})")


if __name__ == "__main__":
    main()
