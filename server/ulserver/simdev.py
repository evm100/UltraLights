"""A simulated node that speaks the device contract: enrolls with a ul_board blob's token,
connects over mutual TLS, applies config/set/live, answers commands.

It is the server track's stand-in for the firmware and doubles as a reference for
how a node is expected to behave (contracts/README.md "Connection sequence").
"""
import asyncio
import datetime as dt
import http.client
import json
import logging
import random
import socket
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiomqtt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from . import contracts

log = logging.getLogger("ul.sim")

DEFAULT_KIND = contracts.DEFAULT_KIND_FOR_TYPE
CAPS = contracts.CAPS_BY_KIND


def make_csr(device_id: str) -> tuple[bytes, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
           .sign(key, hashes.SHA256()))
    kp = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return kp, csr.public_bytes(serialization.Encoding.PEM).decode()


def _ctx(server_ca: str | None, check_hostname: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=server_ca) if server_ca else ssl.create_default_context()
    ctx.check_hostname = check_hostname
    return ctx


class _HTTPS(http.client.HTTPSConnection):
    """HTTPS to a fixed IP while checking the certificate against the real host name."""

    def __init__(self, server: str, connect_ip: str | None, port: int, context: ssl.SSLContext):
        super().__init__(server, port, context=context, timeout=15)
        self._server, self._ip = server, connect_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip or self._server, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server)


def enroll(payload: dict, mac: str, connect_ip: str | None = None, https_port: int = 443) -> dict:
    prov, board = payload["provision"], payload["board"]
    key, csr = make_csr(prov["device_id"])
    req = {"device_id": prov["device_id"], "token": prov["token"], "csr": csr, "chip": board["chip"], "mac": mac,
           "fw": "0.0.1", "profile": f"{board['chip'].removeprefix('esp32')}-sim",
           "board_crc": contracts.ulboard().crc_hex(payload)}
    conn = _HTTPS(prov["server"], connect_ip, https_port, _ctx(prov.get("server_ca")))
    conn.request("POST", "/api/v1/enroll", json.dumps(req), {"Content-Type": "application/json"})
    r = conn.getresponse()
    body = json.loads(r.read())
    if r.status != 200:
        raise RuntimeError(f"enrollment failed: {r.status} {body}")
    return {"key": key.decode(), "cert": body["cert"], "chain": body["ca_chain"], "mqtt": body["mqtt"]}


@dataclass
class SimDevice:
    device_id: str
    board: dict[str, Any]
    cert_file: Path
    key_file: Path
    host: str
    port: int = 8883
    server_ca: str | None = None
    connect_ip: str | None = None
    board_crc: str = "00000000"
    mac: str = "02:00:00:00:00:01"
    outputs: list[dict] = field(default_factory=list)
    state: dict[str, dict] = field(default_factory=dict)
    last_seq: dict[str, int] = field(default_factory=dict)
    config_rev: int = 0
    config_error: str | None = None
    received: list[tuple[str, dict]] = field(default_factory=list)
    client: aiomqtt.Client | None = None
    connected: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self._apply_outputs(contracts.default_outputs(self.board))

    def t(self, kind: str, out: str | None = None) -> str:
        return f"ul/v1/d/{self.device_id}/{kind}" + (f"/{out}" if out else "")

    def _apply_outputs(self, outs: list[dict]) -> None:
        self.outputs = [{**o, "caps": CAPS[o["kind"]]} for o in outs]
        keep = {o["key"] for o in self.outputs}
        self.state = {k: v for k, v in self.state.items() if k in keep}
        for o in self.outputs:
            self.state.setdefault(o["key"], {"on": False, **({"level": 1.0} if "level" in o["caps"] else {})})

    def info(self) -> dict:
        d = {"v": 1, "id": self.device_id, "chip": self.board["chip"], "fw": "0.0.1",
             "profile": f"{self.board['chip'].removeprefix('esp32')}-sim", "idf": "sim",
             "board": self.board["board"], "board_crc": self.board_crc, "mac": self.mac,
             "channels": self.board["channels"], "outputs": self.outputs, "config_rev": self.config_rev}
        if self.config_error:
            d["config_error"] = self.config_error
        return d

    async def pub(self, topic: str, payload: dict, schema: str, qos: int = 1, retain: bool = False) -> None:
        contracts.validate(schema, payload)
        assert self.client is not None
        await self.client.publish(topic, json.dumps(payload), qos=qos, retain=retain)

    async def publish_state(self, key: str, src: str = "device", seq: int = 0) -> None:
        await self.pub(self.t("state", key), {**self.state[key], "src": src, "seq": seq}, "state", retain=True)

    def tls(self) -> ssl.SSLContext:
        ctx = _ctx(self.server_ca, check_hostname=self.connect_ip is None)
        ctx.load_cert_chain(self.cert_file, self.key_file)
        return ctx

    async def run(self, stop: asyncio.Event | None = None) -> None:
        will = aiomqtt.Will(self.t("status"), json.dumps({"online": False}), qos=1, retain=True)
        async with aiomqtt.Client(self.connect_ip or self.host, self.port, identifier=self.device_id,
                                  tls_context=self.tls(), will=will, keepalive=30, clean_session=True) as c:
            self.client = c
            await c.subscribe([(self.t("config"), 1), (self.t("set", "+"), 1), (self.t("live", "+"), 0), (self.t("cmd"), 1)])
            await self.pub(self.t("status"), {"online": True}, "status", retain=True)
            await self.pub(self.t("info"), self.info(), "info", retain=True)
            await self.pub(self.t("health"), self.health(), "health", qos=0)
            for o in self.outputs:
                await self.publish_state(o["key"])
            self.connected.set()
            reader = asyncio.create_task(self._read(c))
            try:
                if stop:
                    await stop.wait()
                    await self.pub(self.t("status"), {"online": False}, "status", retain=True)
                else:
                    await reader
            finally:
                reader.cancel()
                self.connected.clear()

    def health(self) -> dict:
        return {"uptime_s": 1, "heap_free": 200000, "heap_min": 180000, "rssi": -55, "ip": "192.168.1.200",
                "reset_reason": "poweron", "wifi_reconnects": 0, "mqtt_reconnects": 0}

    async def _read(self, c: aiomqtt.Client) -> None:
        async for m in c.messages:
            topic = str(m.topic)
            if not m.payload:
                if topic == self.t("config"):  # empty config = back to the default mapping
                    self.config_rev, self.config_error = 0, None
                    self._apply_outputs(contracts.default_outputs(self.board))
                    await self.pub(self.t("info"), self.info(), "info", retain=True)
                continue
            doc = json.loads(m.payload)
            self.received.append((topic, doc))
            parts = topic.split("/")
            kind = parts[4]
            try:
                if kind == "config":
                    await self._on_config(doc)
                elif kind in ("set", "live"):
                    await self._on_state(kind, parts[5], doc)
                elif kind == "cmd":
                    await self._on_cmd(doc)
            except contracts.ContractError as e:
                log.warning("bad %s from server: %s", kind, e)

    async def _on_config(self, doc: dict) -> None:
        contracts.validate("config", doc)
        errs = contracts.check_config(self.board, doc)
        if errs:
            self.config_error = errs[0][:200]
            await self.pub(self.t("evt"), {"type": "error", "code": "config_rejected", "detail": self.config_error}, "evt")
        else:
            self.config_error = None
            self.config_rev = doc["rev"]
            self._apply_outputs(doc["outputs"])
        await self.pub(self.t("info"), self.info(), "info", retain=True)

    async def _on_state(self, kind: str, key: str, doc: dict) -> None:
        contracts.validate(kind, doc)
        if key not in self.state:
            await self.pub(self.t("evt"), {"type": "error", "code": "no_such_output", "out": key}, "evt")
            return
        if doc["seq"] <= self.last_seq.get(key, 0):
            return  # stale or already applied
        self.last_seq[key] = doc["seq"]
        caps = next(o["caps"] for o in self.outputs if o["key"] == key)
        for f in ("on", "level", "cct", "color", "white", "effect"):
            if f in doc and (f == "on" or f in caps):
                self.state[key][f] = doc[f]
        log.info("%s %s -> %s", kind, key, self.state[key])
        await self.publish_state(key, doc.get("src", "device"), doc["seq"])

    async def _on_cmd(self, doc: dict) -> None:
        contracts.validate("cmd", doc)
        ok, err = True, None
        if doc["op"] == "pulse" and doc["out"] not in self.state:
            ok, err = False, "no_such_output"
        elif doc["op"] in ("ota", "selftest"):
            ok, err = False, "unsupported"
        res = {"type": "cmd_result", "cmd_id": doc["id"], "ok": ok}
        if err:
            res["error"] = err
        await self.pub(self.t("evt"), res, "evt")


def random_mac() -> str:
    return "02:" + ":".join(f"{random.randrange(256):02x}" for _ in range(5))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
