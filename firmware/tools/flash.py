#!/usr/bin/env python3
"""Flash a firmware package and/or a ul_board descriptor over USB with esptool.

CLI fallback for the node builder's Web Serial flashing. The descriptor is either a
ready blob (--board-bin, e.g. downloaded from the builder) or a partition payload JSON
(--board-json, contracts/schemas/partition.schema.json) encoded here with
contracts/tools/ulboard.py.

    # new node: erase, firmware + descriptor
    firmware/tools/flash.py -p /dev/ttyACM0 --erase build/packages/c3-lights-0.1.0.zip --board-json node.json
    # change pins only: rewrite just the descriptor, keep firmware, Wi-Fi and identity
    firmware/tools/flash.py -p /dev/ttyACM0 build/packages/c3-lights-0.1.0.zip --board-json node.json --descriptor-only
    # print the esptool command without running it
    firmware/tools/flash.py --dry-run build/packages/c3-lights-0.1.0.zip --board-json node.json

--erase wipes the whole chip (Wi-Fi, identity, saved state). Use it when converting a
node from older firmware: the old NVS layout and partition table don't match ours.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contracts" / "tools"))
import ulboard  # noqa: E402


def esptool_cmd() -> list[str]:
    idf = os.environ.get("IDF_PATH")
    py = os.environ.get("IDF_PYTHON_ENV_PATH")
    python = str(Path(py) / "bin" / "python") if py else sys.executable
    if idf and (Path(idf) / "components" / "esptool_py" / "esptool" / "esptool.py").exists():
        return [python, str(Path(idf) / "components" / "esptool_py" / "esptool" / "esptool.py")]
    return [python, "-m", "esptool"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", type=Path, help="firmware package .zip from tools/package.py")
    ap.add_argument("-p", "--port", help="serial port, e.g. /dev/ttyACM0")
    ap.add_argument("-b", "--baud", type=int, default=460800)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--board-json", type=Path, help="partition payload JSON to encode and write")
    g.add_argument("--board-bin", type=Path, help="ready ul_board blob to write")
    ap.add_argument("--descriptor-only", action="store_true", help="write only the descriptor")
    ap.add_argument("--erase", action="store_true", help="erase the whole flash first")
    ap.add_argument("--dry-run", action="store_true", help="print the command, flash nothing")
    args = ap.parse_args()

    with zipfile.ZipFile(args.package) as z:
        manifest = json.loads(z.read("manifest.json"))
        work = Path(tempfile.mkdtemp(prefix="ulflash-"))
        z.extractall(work)
    chip = manifest["chip"]
    board = manifest["board_partition"]

    blob_path = None
    if args.board_json:
        payload = json.loads(args.board_json.read_text())
        if payload["board"]["chip"] != chip:
            sys.exit(f"descriptor is for {payload['board']['chip']}, package is for {chip}")
        blob = ulboard.encode(payload, board["size"])
        blob_path = work / "ul_board.bin"
        blob_path.write_bytes(blob)
        print(f"descriptor '{payload['board']['board']}' for {payload['provision']['device_id']}, "
              f"crc {ulboard.crc_hex(payload)}")
    elif args.board_bin:
        blob = args.board_bin.read_bytes()
        ulboard.decode(blob)  # refuses a damaged blob
        if len(blob) > board["size"]:
            sys.exit("blob is larger than the ul_board partition")
        blob_path = args.board_bin
    if args.descriptor_only and not blob_path:
        sys.exit("--descriptor-only needs --board-json or --board-bin")
    if args.descriptor_only and args.erase:
        sys.exit("--erase would wipe the firmware; drop --descriptor-only or --erase")

    base = esptool_cmd() + ["--chip", chip, "-b", str(args.baud), "--before", "default-reset",
                            "--after", "hard-reset"]
    if args.port:
        base += ["-p", args.port]
    writes = []
    if not args.descriptor_only:
        writes += [(f["offset"], work / f["name"]) for f in manifest["files"]]
    if blob_path:
        writes.append((board["offset"], blob_path))
    if not writes:
        sys.exit("nothing to write")
    write = base + ["write-flash", "--flash-mode", manifest["flash"]["mode"],
                    "--flash-size", manifest["flash"]["size"], "--flash-freq", manifest["flash"]["freq"]]
    for off, path in writes:
        write += [hex(off), str(path)]
    cmds = ([base + ["erase-flash"]] if args.erase else []) + [write]

    for c in cmds:
        print(shlex.join(c))
    if args.dry_run:
        return
    if not args.port:
        sys.exit("give the serial port with -p (or use --dry-run)")
    for c in cmds:
        subprocess.run(c, check=True)


if __name__ == "__main__":
    main()
