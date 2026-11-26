"""Turning user intent into contract messages: set, live, config, cmd.

All publishing goes through a publisher with Bridge.publish's signature, so tests
can capture and schema-check every outgoing payload without a broker.
"""
import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import contracts
from .models import AuditLog, Node, Output
from .realtime import Coalescer, Key, SeqAllocator

log = logging.getLogger("ul.control")

STATE_FIELDS = ("on", "level", "cct", "color", "white", "effect")
LIVE_FIELDS = ("on", "level", "cct", "color", "white", "fade_ms")
FIELD_CAP = {"level": "level", "cct": "cct", "color": "color", "white": "white", "effect": "effect"}

Publisher = Callable[..., Awaitable[None]]


class ControlError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class OutputInfo:
    node_id: int
    home_id: int | None
    kind: str
    caps: list[str]
    meta: dict[str, Any]
    desired: dict[str, Any] = field(default_factory=dict)
    dev: str = ""
    room_id: int | None = None  # for guest grants scoped to rooms


def topic(dev: str, kind: str, out: str | None = None) -> str:
    return f"ul/v1/d/{dev}/{kind}" + (f"/{out}" if out else "")


def _caps(o: OutputInfo) -> list[str]:
    return o.caps or contracts.CAPS_BY_KIND.get(o.kind, [])


def filter_fields(o: OutputInfo, fields: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    """Keep only fields the output supports; on/fade_ms always pass for dimmable/switchable outputs."""
    caps = _caps(o)
    out = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in FIELD_CAP and FIELD_CAP[k] not in caps:
            continue
        if k == "on" and "onoff" not in caps:
            continue
        out[k] = v
    return out


def build_set(o: OutputInfo, fields: dict[str, Any]) -> dict[str, Any]:
    """Full desired state: previous desired + new fields, with defaults for anything never set."""
    caps = _caps(o)
    m = {k: v for k, v in o.desired.items() if k in STATE_FIELDS}
    m.update(fields)
    p: dict[str, Any] = {"on": bool(m.get("on", True))}
    if "level" in caps:
        p["level"] = m.get("level", 1.0)
    if "cct" in caps:
        lo, hi = o.meta.get("cct_range", [2700, 6500])
        p["cct"] = min(max(int(m.get("cct", (lo + hi) // 2)), lo), hi)
    if "color" in caps:
        p["color"] = m.get("color", [255, 255, 255])
    if "white" in caps:
        p["white"] = m.get("white", 0.0)
    if "effect" in caps and "effect" in m:
        p["effect"] = m["effect"]
    if "fade_ms" in fields:
        p["fade_ms"] = fields["fade_ms"]
    return p


class Control:
    def __init__(self, publish: Publisher, sm: async_sessionmaker[AsyncSession] | None = None,
                 seq: SeqAllocator | None = None, live_interval: float = 0.05):
        self.publish = publish
        self.sm = sm
        self.seq = seq or SeqAllocator()
        self.outputs: dict[Key, OutputInfo] = {}
        self.coalescer = Coalescer(self._flush_live, interval=live_interval)
        self.cmd_waiters: dict[str, asyncio.Future] | None = None  # set by the app to the bridge's dict

    # ---------- cache ----------
    async def load_all(self, db: AsyncSession) -> None:
        nodes = {n.id: n for n in (await db.execute(select(Node))).scalars()}
        self.outputs.clear()
        for o in (await db.execute(select(Output))).scalars():
            n = nodes[o.node_id]
            self._cache(n, o)

    async def load_node(self, db: AsyncSession, node: Node) -> None:
        for k in [k for k in self.outputs if k[0] == node.device_id]:
            del self.outputs[k]
        for o in (await db.execute(select(Output).where(Output.node_id == node.id))).scalars():
            self._cache(node, o)

    def _cache(self, n: Node, o: Output) -> None:
        self.outputs[(n.device_id, o.key)] = OutputInfo(
            node_id=n.id, home_id=n.home_id, kind=o.kind, caps=list(o.caps or []), meta=dict(o.meta or {}),
            dev=n.device_id, room_id=o.room_id, desired={k: v for k, v in (o.desired or {}).items() if k in STATE_FIELDS})

    def get(self, dev: str, out: str) -> OutputInfo:
        o = self.outputs.get((dev, out))
        if o is None:
            raise ControlError("no_such_output", f"{dev}/{out}")
        return o

    # ---------- live and set ----------
    def live(self, dev: str, out: str, fields: dict[str, Any], src: str) -> None:
        o = self.get(dev, out)
        f = filter_fields(o, fields, LIVE_FIELDS)
        if not f:
            return
        # Validate eagerly (with a dummy seq) so the caller hears about bad values.
        try:
            contracts.validate("live", {**f, "seq": 1})
        except contracts.ContractError as e:
            raise ControlError("invalid", e.message) from None
        o.desired.update({k: v for k, v in f.items() if k in STATE_FIELDS})
        self.coalescer.submit((dev, out), f, src)

    async def _flush_live(self, key: Key, partial: dict[str, Any], src: str | None) -> None:
        payload = dict(partial)
        if src:
            payload["src"] = src
        payload["seq"] = self.seq.next(key)
        try:
            await self.publish(topic(key[0], "live", key[1]), payload, schema="live", qos=0, retain=False)
        except Exception as e:  # noqa: BLE001 - live updates are best effort
            log.warning("live publish failed for %s: %s", key, e)

    async def set(self, dev: str, out: str, fields: dict[str, Any], src: str) -> dict[str, Any]:
        o = self.get(dev, out)
        f = filter_fields(o, fields, STATE_FIELDS + ("fade_ms",))
        self.coalescer.cancel((dev, out))
        payload = build_set(o, f)
        payload["src"] = src
        payload["seq"] = self.seq.next((dev, out))
        try:
            contracts.validate("set", payload)
        except contracts.ContractError as e:
            raise ControlError("invalid", e.message) from None
        await self.publish(topic(dev, "set", out), payload, schema="set", qos=1, retain=True)
        o.desired = {k: v for k, v in payload.items() if k in STATE_FIELDS}
        if self.sm:
            async with self.sm() as db:
                row = (await db.execute(select(Output).where(Output.node_id == o.node_id, Output.key == out))).scalar_one_or_none()
                if row is not None:
                    row.desired = payload
                    await db.commit()
        return payload

    # ---------- config ----------
    async def push_config(self, node: Node) -> None:
        if node.config is None:
            await self.publish(topic(node.device_id, "config"), None, schema=None, qos=1, retain=True)
        else:
            await self.publish(topic(node.device_id, "config"), node.config, schema="config", qos=1, retain=True)

    async def clear_device(self, dev: str, keys: list[str]) -> None:
        """Remove every retained message for a deleted node, in both directions."""
        for t in ("config", "status", "info"):
            await self.publish(topic(dev, t), None, schema=None, qos=1, retain=True)
        for k in keys:
            await self.publish(topic(dev, "set", k), None, schema=None, qos=1, retain=True)
            await self.publish(topic(dev, "state", k), None, schema=None, qos=1, retain=True)
        for k in [k for k in self.outputs if k[0] == dev]:
            del self.outputs[k]

    # ---------- commands ----------
    async def cmd(self, dev: str, op: str, args: dict[str, Any], src: str, wait: float = 0,
                  cid: str | None = None) -> dict[str, Any]:
        """cid: a caller-chosen command id, when the caller must record it before the device can answer."""
        cid = cid or str(uuid.uuid4())
        payload = {"id": cid, "op": op, "ts": int(time.time()), "src": src, **args}
        try:
            contracts.validate("cmd", payload)
        except contracts.ContractError as e:
            raise ControlError("invalid", e.message) from None
        fut: asyncio.Future | None = None
        if wait and self.cmd_waiters is not None:
            fut = asyncio.get_running_loop().create_future()
            self.cmd_waiters[cid] = fut
        try:
            await self.publish(topic(dev, "cmd"), payload, schema="cmd", qos=1, retain=False)
            if fut is not None:
                try:
                    return {"id": cid, "result": await asyncio.wait_for(fut, wait)}
                except asyncio.TimeoutError:
                    return {"id": cid, "result": None}
            return {"id": cid}
        finally:
            if self.cmd_waiters is not None:
                self.cmd_waiters.pop(cid, None)

    async def audit(self, db: AsyncSession, src: str, action: str, target: str | None, detail: dict | None = None) -> None:
        db.add(AuditLog(src=src, action=action, target=target, detail=detail))
