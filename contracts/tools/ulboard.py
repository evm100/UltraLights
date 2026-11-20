#!/usr/bin/env python3
"""Reference encoder/decoder for the ul_board partition blob (see board-partition.md).

    python ulboard.py encode payload.json out.bin [--size 4096]
    python ulboard.py decode in.bin
"""
import json
import struct
import sys
import zlib

MAGIC = b"ULBD"
FORMAT = 1
HEADER = struct.Struct("<4sHHII")  # magic, format, flags, payload length, crc32(payload)
DEFAULT_SIZE = 4096


def canonical(payload: dict) -> bytes:
    """Compact, key-sorted UTF-8 JSON, so the same payload always gives the same CRC."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def encode(payload: dict, size: int = DEFAULT_SIZE) -> bytes:
    body = canonical(payload)
    if HEADER.size + len(body) > size:
        raise ValueError(f"payload is {len(body)} bytes, max {size - HEADER.size}")
    blob = HEADER.pack(MAGIC, FORMAT, 0, len(body), zlib.crc32(body)) + body
    return blob + b"\xff" * (size - len(blob))


def decode(blob: bytes) -> dict:
    magic, fmt, _flags, length, crc = HEADER.unpack_from(blob)
    if magic != MAGIC:
        raise ValueError("no descriptor (bad magic)")
    if fmt != FORMAT:
        raise ValueError(f"unsupported format {fmt}")
    body = blob[HEADER.size:HEADER.size + length]
    if len(body) != length or zlib.crc32(body) != crc:
        raise ValueError("crc mismatch")
    return json.loads(body)


def crc_hex(payload: dict) -> str:
    return f"{zlib.crc32(canonical(payload)):08x}"


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "encode":
        size = int(sys.argv[sys.argv.index("--size") + 1]) if "--size" in sys.argv else DEFAULT_SIZE
        with open(sys.argv[2]) as f:
            payload = json.load(f)
        with open(sys.argv[3], "wb") as f:
            f.write(encode(payload, size))
        print(crc_hex(payload))
    elif len(sys.argv) == 3 and sys.argv[1] == "decode":
        with open(sys.argv[2], "rb") as f:
            print(json.dumps(decode(f.read()), indent=2))
    else:
        sys.exit(__doc__)
