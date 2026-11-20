"""MQTT bridge: the server's only connection to devices.

Inbound (info, status, state/+, evt, health) is validated against the contract,
stored, and fanned out to browsers. Outbound payloads are validated before they
are published (see publish()). Runs inside the API process.
"""
import asyncio
import collections
import datetime as dt
import json
import logging
import re
import ssl
from typing import TYPE_CHECKING, Any

import aiomqtt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import contracts
from .config import Settings
from .hub import Hub
from .models import AuditLog, Node, Output
from .views import device_view

if TYPE_CHECKING:
    from .control import Control

log = logging.getLogger("ul.bridge")

TOPIC_RE = re.compile(r"^ul/v1/d/(ul-[0-9a-f]{6})/(info|status|evt|health|state/([a-z][a-z0-9_]{0,15}))$")
SUBSCRIPTIONS = ["info", "status", "state/+", "evt", "health"]


def tls_context(s: Settings) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=s.mqtt_ca_file) if s.mqtt_ca_file else ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(s.pki_dir / "ul-server.crt", s.pki_dir / "ul-server.key")
    if not s.mqtt_check_hostname:
        ctx.check_hostname = False
    return ctx


class NotConnected(RuntimeError):
    pass


class Bridge:
    def __init__(self, settings: Settings, hub: Hub, sm: async_sessionmaker[AsyncSession]):
        self.s = settings
        self.hub = hub
        self.sm = sm
        self.control: "Control | None" = None
        self.client: aiomqtt.Client | None = None
        self.connected = asyncio.Event()
        self.cmd_waiters: dict[str, asyncio.Future] = {}
        self.contract_errors: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=10))
        self._stop = False

    # ---------- outbound ----------
    async def publish(self, topic: str, payload: dict[str, Any] | None, *, schema: str | None, qos: int, retain: bool) -> None:
        """payload None clears a retained topic. Otherwise it must match the contract."""
        if payload is not None and schema:
            contracts.validate(schema, payload)
        if self.client is None or not self.connected.is_set():
            raise NotConnected("broker not connected")
        data = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        await self.client.publish(topic, data, qos=qos, retain=retain)

    # ---------- connection loop ----------
    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                async with aiomqtt.Client(
                    self.s.mqtt_host,
                    self.s.mqtt_port,
                    identifier="ul-server",
                    tls_context=tls_context(self.s),
                    keepalive=30,
                    clean_session=True,
                ) as client:
                    self.client = client
                    await client.subscribe([(f"ul/v1/d/+/{t}", 1) for t in SUBSCRIPTIONS])
                    self.connected.set()
                    backoff = 1.0
                    log.info("connected to %s:%s", self.s.mqtt_host, self.s.mqtt_port)
                    async for msg in client.messages:
                        try:
                            await self.handle(str(msg.topic), bytes(msg.payload or b""))
                        except Exception:  # noqa: BLE001 - one bad message must not kill the bridge
                            log.exception("error handling %s", msg.topic)
            except aiomqtt.MqttError as e:
                log.warning("broker connection lost: %s; retrying in %.0fs", e, backoff)
            except (OSError, ssl.SSLError) as e:
                log.warning("cannot reach broker: %s; retrying in %.0fs", e, backoff)
            finally:
                self.connected.clear()
                self.client = None
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop = True

    # ---------- inbound ----------
    def _contract_error(self, dev: str, topic: str, err: str) -> None:
        log.warning("contract violation from %s on %s: %s", dev, topic, err)
        self.contract_errors[dev].append({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "topic": topic, "error": err})

    async def handle(self, topic: str, payload: bytes) -> None:
        m = TOPIC_RE.match(topic)
        if not m:
            return
        dev, kind, out = m.group(1), m.group(2), m.group(3)
        if not payload:
            return  # a cleared retained message
        try:
            doc = json.loads(payload)
        except ValueError:
            self._contract_error(dev, topic, "not JSON")
            return
        schema = "state" if kind.startswith("state/") else kind
        try:
            contracts.validate(schema, doc)
        except contracts.ContractError as e:
            self._contract_error(dev, topic, str(e))
            return
        async with self.sm() as db:
            node = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one_or_none()
            if node is None:
                log.info("message from unknown device %s ignored", dev)
                return
            now = dt.datetime.now(dt.timezone.utc)
            if schema == "status":
                node.online = doc["online"]
                node.last_seen = now
                await db.commit()
                await self._push_device(db, node)
            elif schema == "info":
                await self._on_info(db, node, doc, now)
            elif schema == "state":
                o = (await db.execute(select(Output).where(Output.node_id == node.id, Output.key == out))).scalar_one_or_none()
                if o is None:
                    return
                o.actual = doc
                node.last_seen = now
                await db.commit()
                self.hub.broadcast({"t": "state", "dev": dev, "out": out, "state": doc}, node.home_id)
            elif schema == "health":
                node.health = doc
                node.last_seen = now
                await db.commit()
                self.hub.broadcast({"t": "health", "dev": dev, "health": doc}, node.home_id, admin_only=True)
            elif schema == "evt":
                if doc["type"] == "cmd_result":
                    fut = self.cmd_waiters.get(doc["cmd_id"])
                    if fut and not fut.done() and "progress" not in doc:
                        fut.set_result(doc)
                if doc["type"] == "error" or (doc["type"] == "cmd_result" and not doc["ok"]):
                    db.add(AuditLog(src="device", action="device_error", target=dev, detail=doc))
                    await db.commit()
                self.hub.broadcast({"t": "evt", "dev": dev, "evt": doc}, node.home_id, admin_only=True)

    async def _on_info(self, db: AsyncSession, node: Node, doc: dict, now: dt.datetime) -> None:
        if doc["id"] != node.device_id:
            self._contract_error(node.device_id, "info", f"info.id {doc['id']} does not match topic")
            return
        if doc["chip"] != node.chip:
            self._contract_error(node.device_id, "info", f"device reports {doc['chip']}, node is {node.chip}")
        node.info = doc
        node.last_seen = now
        existing = {o.key: o for o in (await db.execute(select(Output).where(Output.node_id == node.id))).scalars()}
        seen = set()
        for od in doc["outputs"]:
            seen.add(od["key"])
            o = existing.get(od["key"])
            if o is None:
                o = Output(node_id=node.id, key=od["key"], kind=od["kind"], name=od.get("name", ""))
                db.add(o)
            o.kind = od["kind"]
            o.caps = od["caps"]
            if od.get("name"):
                o.name = od["name"]
            o.meta = {k: od[k] for k in ("cct_range", "pixels", "max_level", "channels") if k in od}
        stale = [k for k in existing if k not in seen]
        if stale:
            await db.execute(delete(Output).where(Output.node_id == node.id, Output.key.in_(stale)))
        await db.commit()
        if self.control:
            await self.control.load_node(db, node)
        await self._push_device(db, node)

    async def _push_device(self, db: AsyncSession, node: Node) -> None:
        outs = list((await db.execute(select(Output).where(Output.node_id == node.id))).scalars())
        self.hub.broadcast({"t": "device", "device": device_view(node, outs, admin=False)}, node.home_id,
                           admin_msg={"t": "device", "device": device_view(node, outs, admin=True)})
