#!/usr/bin/env python3
"""Validate JSON the firmware encoders produced (host tests) against contracts/schemas.

    python3 firmware/tools/check_encoded.py <dir>   # <dir>/<schema>/*.json
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
schemas = {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in SCHEMAS.glob("*.schema.json")}
registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())

root = Path(sys.argv[1])
bad = n = 0
for d in sorted(p for p in root.iterdir() if p.is_dir()):
    v = Draft202012Validator(schemas[d.name], registry=registry, format_checker=FormatChecker())
    for f in sorted(d.glob("*.json")):
        n += 1
        errs = list(v.iter_errors(json.loads(f.read_text())))
        if errs:
            bad += 1
            print(f"FAIL {d.name}/{f.name}: {errs[0].message}")
print(f"{n} encoded messages checked against schemas, {bad} failures")
sys.exit(1 if bad or n == 0 else 0)
