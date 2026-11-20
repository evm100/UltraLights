"""Firmware packages: a .zip with manifest.json (contracts/schemas/firmware-manifest) and its files."""
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from . import contracts

MAX_ZIP = 16 * 1024 * 1024


class PackageError(ValueError):
    pass


def read_package(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate a package and return (manifest, {name: bytes})."""
    if len(data) > MAX_ZIP:
        raise PackageError("package larger than 16 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("not a zip file") from None
    names = set(z.namelist())
    if "manifest.json" not in names:
        raise PackageError("manifest.json missing at the root of the zip")
    try:
        manifest = json.loads(z.read("manifest.json"))
        contracts.validate("firmware-manifest", manifest)
    except (ValueError, contracts.ContractError) as e:
        raise PackageError(f"manifest: {e}") from None
    if not manifest["profile"].startswith(manifest["chip"].removeprefix("esp32")):
        raise PackageError(f"profile {manifest['profile']} does not match chip {manifest['chip']}")
    files: dict[str, bytes] = {}
    ranges = []
    for f in manifest["files"]:
        if f["name"] not in names:
            raise PackageError(f"{f['name']} listed in manifest but missing from zip")
        b = z.read(f["name"])
        if len(b) != f["size"]:
            raise PackageError(f"{f['name']}: size {len(b)} != manifest {f['size']}")
        if hashlib.sha256(b).hexdigest() != f["sha256"]:
            raise PackageError(f"{f['name']}: sha256 mismatch")
        files[f["name"]] = b
        ranges.append((f["offset"], f["offset"] + f["size"], f["name"]))
    apps = [f["name"] for f in manifest["files"] if f["role"] == "app"]
    if apps != [manifest["app"]]:
        raise PackageError("manifest.app must name the single file with role app")
    bp = manifest["board_partition"]
    ranges.append((bp["offset"], bp["offset"] + bp["size"], "ul_board"))
    ranges.sort()
    for a, b in zip(ranges, ranges[1:]):
        if b[0] < a[1]:
            raise PackageError(f"{a[2]} overlaps {b[2]}")
    return manifest, files


def store(root: Path, manifest: dict[str, Any], files: dict[str, bytes]) -> Path:
    d = root / manifest["profile"] / manifest["version"]
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for name, b in files.items():
        (d / name).write_bytes(b)
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return d


def file_path(root: Path, manifest: dict[str, Any], name: str) -> Path | None:
    if name != "manifest.json" and name not in {f["name"] for f in manifest["files"]}:
        return None
    p = root / manifest["profile"] / manifest["version"] / name
    return p if p.is_file() else None
