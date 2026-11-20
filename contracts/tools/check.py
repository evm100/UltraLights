#!/usr/bin/env python3
"""Validate every contract example against its schema.

examples/<schema>/*.json must validate; examples/invalid/<schema>/*.json must not.
examples/board-rules/*.json must validate as boards AND produce exactly their
"_expect" result from the reference pin check. Also checks the ul_board test
vector round-trips. Exit status 1 on any failure.

    python3 contracts/tools/check.py      (needs jsonschema >= 4.18)
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pins  # noqa: E402
import ulboard  # noqa: E402

schemas = {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in (ROOT / "schemas").glob("*.schema.json")}
registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())


def validator(name):
    return Draft202012Validator(schemas[name], registry=registry, format_checker=FormatChecker())


failures = 0
count = 0


def fail(msg):
    global failures
    failures += 1
    print("FAIL", msg)


for d in sorted((ROOT / "examples").iterdir()):
    if not d.is_dir() or d.name in ("invalid", "board-rules", "vectors"):
        continue
    v = validator(d.name)
    for f in sorted(d.glob("*.json")):
        count += 1
        errs = list(v.iter_errors(json.loads(f.read_text())))
        if errs:
            fail(f"{f.relative_to(ROOT)}: {errs[0].message}")

for d in sorted((ROOT / "examples" / "invalid").iterdir()):
    v = validator(d.name)
    for f in sorted(d.glob("*.json")):
        count += 1
        if v.is_valid(json.loads(f.read_text())):
            fail(f"{f.relative_to(ROOT)} should be rejected")

bv = validator("board")
for f in sorted((ROOT / "examples" / "board-rules").glob("*.json")):
    count += 1
    doc = json.loads(f.read_text())
    expect = doc.pop("_expect")
    if not bv.is_valid(doc):
        fail(f"{f.relative_to(ROOT)}: not a valid board")
    got = pins.check_board(doc)
    if got != expect:
        fail(f"{f.relative_to(ROOT)}: expected {expect}, got {got}")

for f in sorted((ROOT / "examples" / "board").glob("*.json")):
    got = pins.check_board(json.loads(f.read_text()))
    if got["errors"]:
        fail(f"{f.relative_to(ROOT)}: pin errors {got['errors']}")

vec = ROOT / "examples" / "vectors"
payload = json.loads((ROOT / "examples" / "partition" / "c3-bench.json").read_text())
blob = (vec / "ul_board.c3-bench.bin").read_bytes()
count += 1
if blob != ulboard.encode(payload) or ulboard.decode(blob) != payload:
    fail("vectors/ul_board.c3-bench.bin does not match partition/c3-bench.json")
if (vec / "ul_board.c3-bench.crc").read_text().strip() != ulboard.crc_hex(payload):
    fail("vectors/ul_board.c3-bench.crc is stale")

print(f"{count} checks, {failures} failures")
sys.exit(1 if failures else 0)
