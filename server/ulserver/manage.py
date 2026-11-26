"""Changes to a home's setup, shared by the owner API (api/homes.py) and the admin panel.

Names and limits for a node's outputs live in its config (contracts/schemas/output), which
is pushed to the device and echoed back in info, so they are written there, never only in
the database. A bridge's outputs have no config: their names are a database override.
"""
import copy
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import contracts
from .access import INPUT_KINDS, LIGHT_KINDS
from .bridge import NotConnected
from .control import Control
from .hub import Hub
from .models import AuditLog, HomeMember, Node, Output, Room

log = logging.getLogger("ul.manage")

# Output settings an owner may change. Channel grouping, curves, cct range and pixel counts
# describe the wiring and stay with the admin's output mapping.
OWNER_OUTPUT_FIELDS = ("name", "room_id", "max_level", "power_on", "max_on_ms")
MAX_PULSE_MS = 7_200_000  # contracts output.max_on_ms / cmd pulse ms ceiling (2 h)
DEFAULTS = {"max_level": 1.0, "power_on": "last"}


class ManageError(Exception):
    def __init__(self, status: int, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


def mapping(node: Node) -> list[dict[str, Any]]:
    """The node's current output definitions: its config, or the default mapping."""
    if node.config is not None:
        return copy.deepcopy(node.config["outputs"])
    return contracts.default_outputs(node.board or {})


def output_settings(node: Node, o: Output) -> dict[str, Any]:
    entry = next((e for e in mapping(node) if e["key"] == o.key), {}) if node.kind == "node" else {}
    v: dict[str, Any] = {"key": o.key, "kind": o.kind, "name": o.name or o.key, "room_id": o.room_id}
    if node.kind == "node" and o.kind == "solenoid":
        v["max_on_ms"] = entry.get("max_on_ms")  # valves always start closed: no power-on choice
    elif node.kind == "node" and o.kind not in INPUT_KINDS:
        v["power_on"] = entry.get("power_on", DEFAULTS["power_on"])
        if o.kind in LIGHT_KINDS:
            v["max_level"] = entry.get("max_level", DEFAULTS["max_level"])
    return v


async def push_device(db: AsyncSession, hub: Hub, node: Node) -> None:
    outs = list((await db.execute(select(Output).where(Output.node_id == node.id))).scalars())
    hub.broadcast_device(node, outs)


async def update_output(db: AsyncSession, control: Control, hub: Hub, node: Node, o: Output,
                        changes: dict[str, Any], src: str) -> dict[str, Any]:
    """Apply name / room / limit changes. Returns {ok, config_rev?, published?}."""
    unknown = set(changes) - set(OWNER_OUTPUT_FIELDS)
    if unknown:
        raise ManageError(422, "invalid", f"unknown fields: {', '.join(sorted(unknown))}")
    if "room_id" in changes and changes["room_id"] is not None:
        r = await db.get(Room, changes["room_id"])
        if r is None or r.home_id != node.home_id:
            raise ManageError(422, "invalid", "room must belong to the device's home")
    limits = {k: changes[k] for k in ("max_level", "power_on", "max_on_ms") if k in changes}
    if limits and node.kind != "node":
        raise ManageError(422, "invalid", "a bridged device has no limits to set")
    if "max_level" in limits and o.kind not in LIGHT_KINDS:
        raise ManageError(422, "invalid", f"a {o.kind} output has no level")
    if "power_on" in limits and (o.kind in INPUT_KINDS or o.kind == "solenoid"):
        raise ManageError(422, "invalid", f"a {o.kind} has no power-on state")
    if "max_on_ms" in limits:
        if o.kind != "solenoid":
            raise ManageError(422, "invalid", "only a valve has a run limit")
        v = limits["max_on_ms"]
        if not isinstance(v, int) or not 60_000 <= v <= MAX_PULSE_MS:
            raise ManageError(422, "invalid", "a valve's run limit is 1 minute to 2 hours")

    if "name" in changes:
        o.name = (changes["name"] or "").strip()
    if "room_id" in changes:
        o.room_id = changes["room_id"]

    result: dict[str, Any] = {"ok": True}
    config_changed = False
    if node.kind == "node" and ("name" in changes or limits):
        outputs = mapping(node)
        entry = next((e for e in outputs if e["key"] == o.key), None)
        if entry is None:
            raise ManageError(409, "not_in_mapping", f"{o.key} is not in the node's output mapping")
        if "name" in changes:
            if o.name:
                entry["name"] = o.name
            else:
                entry.pop("name", None)
        for k, v in limits.items():
            if k == "max_on_ms":
                entry[k] = v  # required for a solenoid, so never dropped
            elif v is None or v == DEFAULTS[k]:
                entry.pop(k, None)
            else:
                entry[k] = v
        cfg = {"v": 1, "rev": node.config_rev + 1, "outputs": outputs}
        errs = contracts.check_config(node.board or {}, cfg)
        if errs:
            raise ManageError(422, "config_invalid", "; ".join(errs))
        if node.config is None or cfg["outputs"] != node.config["outputs"]:
            node.config, node.config_rev = cfg, cfg["rev"]
            config_changed = True
            result["config_rev"] = cfg["rev"]
    db.add(AuditLog(src=src, action="output_changed", target=f"{node.device_id}/{o.key}"[:64],
                    detail={k: changes[k] for k in changes}))
    await db.commit()
    await control.load_node(db, node)
    if config_changed:
        try:
            await control.push_config(node)
            result["published"] = True
        except NotConnected:
            result["published"] = False  # retained on the next connect
    await push_device(db, hub, node)
    return result


async def rename_device(db: AsyncSession, hub: Hub, node: Node, name: str, src: str) -> None:
    node.name = name.strip()
    db.add(AuditLog(src=src, action="device_renamed", target=node.device_id, detail={"name": node.name}))
    await db.commit()
    await push_device(db, hub, node)


async def set_member(db: AsyncSession, home_id: int, user_id: int, role: str | None, src: str) -> None:
    """Change a member's role, or remove them (role None). A home always keeps an owner."""
    m = await db.get(HomeMember, (home_id, user_id))
    if m is None:
        raise ManageError(404, "not_a_member")
    if m.role == "owner" and role != "owner":
        owners = (await db.execute(select(func.count()).select_from(HomeMember).where(
            HomeMember.home_id == home_id, HomeMember.role == "owner"))).scalar_one()
        if owners <= 1:
            raise ManageError(409, "last_owner", "a home needs at least one owner; make someone else owner first")
    if role is None:
        await db.delete(m)
        db.add(AuditLog(src=src, action="member_removed", target=f"home:{home_id}", detail={"user_id": user_id}))
    else:
        m.role = role
        db.add(AuditLog(src=src, action="member_set", target=f"home:{home_id}", detail={"user_id": user_id, "role": role}))
    await db.commit()
