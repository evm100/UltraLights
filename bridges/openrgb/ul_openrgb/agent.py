"""The agent: one UltraLights device for the whole PC.

MQTT follows contracts/README.md "Connection sequence", with the bridge branch of info:
status online, info (parts, sources, reachability), state per part; then retained set
messages arrive and are applied. There is no config topic: parts come from agent.toml.
"""
import asyncio
import json
import logging
import os
import socket
import ssl
import time
from typing import Any

import aiomqtt

from . import __version__
from .config import Config
from .sources import Backend, SourceStatus, SourceWorker, make_backend
from .state import Parts, parse_update

log = logging.getLogger("ul.openrgb")
CAPS = ["onoff", "level", "color"]
SET_FADE_MS = 400  # contract defaults
LIVE_FADE_MS = 60
STATE_INTERVAL_S = 0.2  # at most 5 state reports per second per part while a live stream runs
IDENTIFY_MS = 5000


def tls_context(cfg: Config) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=cfg.server_ca) if cfg.server_ca else ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg.cert_file, cfg.key_file)
    if cfg.connect:
        ctx.check_hostname = False  # dev only: dialling an IP; the chain is still verified
    return ctx


class Agent:
    def __init__(self, cfg: Config, backends: dict[str, Backend] | None = None):
        if not cfg.device_id:
            raise ValueError("no device_id in the config; run `ul-openrgb enroll` first")
        self.cfg = cfg
        self.dev = cfg.device_id
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.parts = Parts(list(cfg.parts), cfg.state_dir / "state.json")
        self.status: dict[str, SourceStatus] = {k: SourceStatus() for k in cfg.sources}
        backends = backends or {}
        self.workers = {
            k: SourceWorker(s, cfg.parts_on(k), self.parts, backends.get(k) or make_backend(s), self._on_status_thread)
            for k, s in cfg.sources.items()}
        self.client: aiomqtt.Client | None = None
        self.connected = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._last_state: dict[str, float] = {}
        self._state_timers: dict[str, asyncio.TimerHandle] = {}
        self._info_dirty = False
        self._save_handle: asyncio.TimerHandle | None = None
        self._stop = asyncio.Event()
        self.mqtt_reconnects = 0

    def t(self, kind: str, out: str | None = None) -> str:
        return f"ul/v1/d/{self.dev}/{kind}" + (f"/{out}" if out else "")

    # ---------------------------------------------------------------- info
    def info(self) -> dict[str, Any]:
        sources = []
        for k, s in self.cfg.sources.items():
            st = self.status[k]
            d: dict[str, Any] = {"key": k, "name": s.name or k, "address": s.address[:80], "online": st.online}
            if st.devices:
                d["devices"] = st.devices
            if st.error and not st.online:
                d["error"] = st.error
            sources.append(d)
        outputs = []
        for p in self.cfg.parts.values():
            st = self.status[p.source]
            leds, found = st.parts.get(p.key, (0, 0))
            outputs.append({"key": p.key, "kind": "rgb", "name": p.name, "caps": CAPS, "part": p.part,
                            "source": p.source, "leds": leds, "reachable": st.online and found > 0})
        return {"v": 1, "id": self.dev, "bridge": "openrgb", "fw": __version__, "host": socket.gethostname()[:64],
                "combine": True, "sources": sources, "outputs": outputs}

    def _on_status_thread(self, key: str, st: SourceStatus) -> None:
        self.status[key] = st
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._schedule_info)

    def _schedule_info(self) -> None:
        if not self._info_dirty:
            self._info_dirty = True
            asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(self._publish_info()))

    async def _publish_info(self) -> None:
        self._info_dirty = False
        if self.connected.is_set():
            await self.pub(self.t("info"), self.info(), retain=True)

    # ---------------------------------------------------------------- MQTT
    async def pub(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        if self.client is None:
            return
        try:
            await self.client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=qos, retain=retain)
        except aiomqtt.MqttError as e:
            log.debug("publish %s failed: %s", topic, e)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        for w in self.workers.values():
            w.start()
        backoff = 1.0
        host = self.cfg.connect or self.cfg.server
        try:
            while not self._stop.is_set():
                will = aiomqtt.Will(self.t("status"), json.dumps({"online": False}), qos=1, retain=True)
                try:
                    async with aiomqtt.Client(host, self.cfg.mqtt_port, identifier=self.dev, tls_context=tls_context(self.cfg),
                                              will=will, keepalive=30, clean_session=True) as c:
                        self.client = c
                        await c.subscribe([(self.t("set", "+"), 1), (self.t("live", "+"), 0), (self.t("cmd"), 1)])
                        await self.pub(self.t("status"), {"online": True}, retain=True)
                        await self.pub(self.t("info"), self.info(), retain=True)
                        for k in self.cfg.parts:
                            await self.pub(self.t("state", k), self.parts.report(k), retain=True)
                        self.connected.set()
                        backoff = 1.0
                        log.info("connected to %s:%s as %s", host, self.cfg.mqtt_port, self.dev)
                        reader = asyncio.create_task(self._read(c))
                        stopper = asyncio.create_task(self._stop.wait())
                        done, _ = await asyncio.wait({reader, stopper}, return_when=asyncio.FIRST_COMPLETED)
                        if stopper in done:
                            await self.pub(self.t("status"), {"online": False}, retain=True)
                        for task in (reader, stopper):
                            task.cancel()
                        for task in done:
                            if task is reader and task.exception():
                                raise task.exception()  # type: ignore[misc]
                except aiomqtt.MqttError as e:
                    log.warning("broker connection lost: %s; retrying in %.0fs", e, backoff)
                except (OSError, ssl.SSLError) as e:
                    log.warning("cannot reach broker %s:%s: %s; retrying in %.0fs", host, self.cfg.mqtt_port, e, backoff)
                finally:
                    self.connected.clear()
                    self.client = None
                if self._stop.is_set():
                    break
                self.mqtt_reconnects += 1
                try:
                    await asyncio.wait_for(self._stop.wait(), backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
        finally:
            for w in self.workers.values():
                w.stop()
            self.parts.save()

    def stop(self) -> None:
        self._stop.set()

    async def _read(self, c: aiomqtt.Client) -> None:
        async for m in c.messages:
            if not m.payload:
                continue  # a cleared retained message
            try:
                doc = json.loads(m.payload)
            except ValueError:
                log.warning("not JSON on %s", m.topic)
                continue
            parts = str(m.topic).split("/")
            kind = parts[4] if len(parts) > 4 else ""
            if kind in ("set", "live") and len(parts) == 6:
                await self._on_update(kind, parts[5], doc)
            elif kind == "cmd":
                await self._on_cmd(doc)

    async def _on_update(self, kind: str, key: str, doc: Any) -> None:
        if key not in self.cfg.parts:
            await self.pub(self.t("evt"), {"type": "error", "code": "no_such_output", "out": key})
            return
        f = parse_update(doc)
        if f is None:
            log.warning("bad %s for %s: %s", kind, key, str(doc)[:200])
            return
        if not self.parts.apply(key, f, SET_FADE_MS if kind == "set" else LIVE_FADE_MS):
            return  # stale
        self.workers[self.cfg.parts[key].source].wake()
        self._report(key, now=kind == "set")
        if kind == "set":
            self._schedule_save()

    def _report(self, key: str, now: bool) -> None:
        """state for a part: right away for set, at most every 200 ms for live (always the last one)."""
        loop = asyncio.get_running_loop()
        wait = 0.0 if now else self._last_state.get(key, 0.0) + STATE_INTERVAL_S - time.monotonic()
        if wait <= 0:
            h = self._state_timers.pop(key, None)
            if h:
                h.cancel()
            self._last_state[key] = time.monotonic()
            asyncio.ensure_future(self.pub(self.t("state", key), self.parts.report(key), retain=True))
        elif key not in self._state_timers:
            def fire() -> None:
                self._state_timers.pop(key, None)
                self._report(key, now=True)
            self._state_timers[key] = loop.call_later(wait, fire)

    def _schedule_save(self) -> None:
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = asyncio.get_running_loop().call_later(2.0, self.parts.save)

    async def _on_cmd(self, doc: Any) -> None:
        if not isinstance(doc, dict) or not isinstance(doc.get("id"), str):
            return
        ok, err = True, None
        op = doc.get("op")
        if op == "identify":
            out = doc.get("out")
            keys = [out] if out else list(self.cfg.parts)
            if out and out not in self.cfg.parts:
                ok, err = False, "no_such_output"
            else:
                ms = doc.get("ms", IDENTIFY_MS)
                ms = ms if isinstance(ms, int) and 500 <= ms <= 60000 else IDENTIFY_MS
                self.parts.identify(keys, ms / 1000)
                for w in self.workers.values():
                    w.wake()
                asyncio.get_running_loop().call_later(ms / 1000 + 0.05, lambda: [w.wake() for w in self.workers.values()])
        elif op == "reboot":
            # Restart the agent (systemd brings it back); never the PC.
            await self.pub(self.t("evt"), {"type": "cmd_result", "cmd_id": doc["id"], "ok": True})
            log.info("restart requested by %s", doc.get("src", "?"))
            self.parts.save()
            asyncio.get_running_loop().call_later(0.5, lambda: os._exit(0))
            return
        else:
            ok, err = False, "unsupported"
        res: dict[str, Any] = {"type": "cmd_result", "cmd_id": doc["id"], "ok": ok}
        if err:
            res["error"] = err
        await self.pub(self.t("evt"), res)
