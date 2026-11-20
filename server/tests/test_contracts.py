"""The server uses the reference contract tools and matches the shared examples byte for byte."""
import json

import pytest
from conftest import CONTRACTS

from ulserver import contracts


def test_blob_matches_vector_byte_for_byte():
    payload = json.loads((CONTRACTS / "examples/partition/c3-bench.json").read_text())
    blob = contracts.ulboard().encode(payload)
    assert blob == (CONTRACTS / "examples/vectors/ul_board.c3-bench.bin").read_bytes()
    assert contracts.ulboard().crc_hex(payload) == (CONTRACTS / "examples/vectors/ul_board.c3-bench.crc").read_text().strip()


@pytest.mark.parametrize("path", sorted((CONTRACTS / "examples/board-rules").glob("*.json")), ids=lambda p: p.stem)
def test_board_rules(path):
    doc = json.loads(path.read_text())
    expect = doc.pop("_expect")
    assert contracts.check_board(doc) == expect


@pytest.mark.parametrize("path", sorted((CONTRACTS / "examples/invalid/board").glob("*.json")), ids=lambda p: p.stem)
def test_board_schema_errors(path):
    res = contracts.check_board(json.loads(path.read_text()))
    assert res["errors"] and res["errors"][0].startswith("schema:")


def test_default_outputs_match_info_example():
    board = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())
    info = json.loads((CONTRACTS / "examples/info/c3-bench-default.json").read_text())
    # Names are optional (the server copies channel names); the mapping itself must match.
    got = [{k: v for k, v in o.items() if k != "name"} for o in contracts.default_outputs(board)]
    want = [{k: v for k, v in o.items() if k not in ("caps", "name")} for o in info["outputs"]]
    assert got == want


def test_config_cross_checks():
    board = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())
    good = json.loads((CONTRACTS / "examples/config/c3-bench-cct.json").read_text())
    assert contracts.check_config(board, good) == []
    bad = {"v": 1, "rev": 2, "outputs": [
        {"key": "a", "kind": "cct", "channels": {"warm": "warm", "cool": "strip"}},
        {"key": "b", "kind": "white", "channels": {"w": "warm"}},
        {"key": "c", "kind": "rgb", "channels": {"r": "nope", "g": "cool", "b": "relay"}},
    ]}
    errs = contracts.check_config(board, bad)
    assert any("role cool needs a pwm channel" in e for e in errs)
    assert any("already used by a" in e for e in errs)
    assert any("nope not on board" in e for e in errs)
