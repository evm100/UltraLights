"""Access to the shared contracts/ folder: schema validators and the reference tools.

Every JSON payload the server sends to a device, or accepts from one, goes through
validate() so the server can't drift from the contract.
"""
import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match
from referencing import Registry, Resource

from .config import get_settings


class ContractError(ValueError):
    def __init__(self, schema: str, message: str, path: str = ""):
        super().__init__(f"{schema}: {message}" + (f" at {path}" if path else ""))
        self.schema = schema
        self.message = message
        self.path = path


def _dir() -> Path:
    return get_settings().contracts_dir


@lru_cache
def _registry() -> tuple[dict[str, dict], Registry]:
    schemas = {
        p.name.removesuffix(".schema.json"): json.loads(p.read_text())
        for p in (_dir() / "schemas").glob("*.schema.json")
    }
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
    return schemas, reg


@lru_cache
def validator(name: str) -> Draft202012Validator:
    schemas, reg = _registry()
    return Draft202012Validator(schemas[name], registry=reg, format_checker=FormatChecker())


def validate(name: str, doc: Any) -> Any:
    """Raise ContractError if doc doesn't match contracts/schemas/<name>.schema.json."""
    err = best_match(validator(name).iter_errors(doc))
    if err is not None:
        raise ContractError(name, err.message, "/".join(str(p) for p in err.absolute_path))
    return doc


def is_valid(name: str, doc: Any) -> bool:
    return validator(name).is_valid(doc)


def _load_tool(name: str) -> ModuleType:
    path = _dir() / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ul_contracts_{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@lru_cache
def pins() -> ModuleType:
    """contracts/tools/pins.py: reference pin-rule check (check_board)."""
    return _load_tool("pins")


@lru_cache
def ulboard() -> ModuleType:
    """contracts/tools/ulboard.py: ul_board partition blob encoder."""
    return _load_tool("ulboard")


@lru_cache
def chips() -> dict:
    return json.loads((_dir() / "chips.json").read_text())


def check_board(board: dict) -> dict:
    """Schema check, then pin rules. Returns {"errors": [...], "warnings": [...]}."""
    errs = [f"schema:{e.message}" for e in validator("board").iter_errors(board)]
    if errs:
        return {"errors": errs[:5], "warnings": []}
    return pins().check_board(board)


ROLES_BY_KIND: dict[str, set[str]] = {
    "white": {"w"},
    "cct": {"warm", "cool"},
    "rgb": {"r", "g", "b"},
    "rgbw": {"r", "g", "b", "w"},
    "rgbcct": {"r", "g", "b", "warm", "cool"},
    "argb": {"data"},
    "relay": {"coil"},
    "solenoid": {"coil"},
    "motion": {"in"},
    "cycle": {"press"},
}

CHANNEL_TYPE_FOR_ROLE = {
    "w": "pwm", "warm": "pwm", "cool": "pwm", "r": "pwm", "g": "pwm", "b": "pwm",
    "data": "argb", "coil": "switch", "in": "motion", "press": "servo",
}

CAPS_BY_KIND: dict[str, list[str]] = {
    "white": ["onoff", "level"],
    "cct": ["onoff", "level", "cct"],
    "rgb": ["onoff", "level", "color"],
    "rgbw": ["onoff", "level", "color", "white"],
    "rgbcct": ["onoff", "level", "color", "cct"],
    "argb": ["onoff", "level", "color"],
    "relay": ["onoff"],
    "solenoid": ["pulse"],
    "motion": ["motion"],
    "cycle": ["onoff", "mode"],
}

DEFAULT_KIND_FOR_TYPE = {"pwm": "white", "argb": "argb", "switch": "relay", "motion": "motion", "servo": "cycle"}
DEFAULT_ROLE_FOR_TYPE = {"pwm": "w", "argb": "data", "switch": "coil", "motion": "in", "servo": "press"}
DEFAULT_MODES = ["on"]  # board.schema.json servo channel default


def cycle_modes(meta: dict) -> list[str]:
    """A cycle output's modes in button order (from the device's info), without "off"."""
    return list(meta.get("modes") or DEFAULT_MODES)


def check_config(board: dict, config: dict) -> list[str]:
    """Schema plus cross-checks against the board: roles fit the kind, channels exist,
    have the right type, and are used once. Returns error strings (empty = ok)."""
    try:
        validate("config", config)
    except ContractError as e:
        return [str(e)]
    errors: list[str] = []
    channels = {c["id"]: c for c in board.get("channels", [])}
    used: dict[str, str] = {}
    keys: set[str] = set()
    for o in config["outputs"]:
        k = o["key"]
        if k in keys:
            errors.append(f"output {k}: duplicate key")
        keys.add(k)
        want = ROLES_BY_KIND[o["kind"]]
        if set(o["channels"]) != want:
            errors.append(f"output {k}: kind {o['kind']} needs roles {sorted(want)}")
        for role, ch in o["channels"].items():
            c = channels.get(ch)
            if c is None:
                errors.append(f"output {k}: channel {ch} not on board")
                continue
            if c["type"] != CHANNEL_TYPE_FOR_ROLE.get(role):
                errors.append(f"output {k}: role {role} needs a {CHANNEL_TYPE_FOR_ROLE.get(role)} channel, {ch} is {c['type']}")
            if ch in used:
                errors.append(f"output {k}: channel {ch} already used by {used[ch]}")
            used[ch] = k
        if "pixels" in o:
            c = channels.get(o["channels"].get("data", ""))
            if c and o["pixels"] > c.get("pixels", 0):
                errors.append(f"output {k}: pixels {o['pixels']} > channel's {c.get('pixels')}")
    return errors


def default_outputs(board: dict) -> list[dict]:
    """The device's default mapping (contracts/README.md): one output per channel."""
    out = []
    for c in board.get("channels", []):
        kind = DEFAULT_KIND_FOR_TYPE[c["type"]]
        o = {"key": c["id"], "kind": kind, "channels": {DEFAULT_ROLE_FOR_TYPE[c["type"]]: c["id"]}}
        if c.get("name"):
            o["name"] = c["name"]
        if kind == "argb":
            o["pixels"] = c["pixels"]
        out.append(o)
    return out
