"""Bridge + simulated device against the real dev broker (mTLS, ACL, retained messages).

Needs `server/tools/dev.sh up` (Mosquitto on :8883 using server/.dev/pki). Skipped otherwise.
"""
import asyncio
import json
import secrets
import socket

import aiomqtt
import pytest
from conftest import CONTRACTS, SERVER

from ulserver import db as dbm
from ulserver.access import Access
from ulserver.bridge import Bridge, tls_context
from ulserver.config import Settings, get_settings
from ulserver.control import Control
from ulserver.hub import Client, Hub
from ulserver.models import Node, Output
from ulserver.pki import DevicePKI, cert_pem, init_dev_tls, key_pem, new_key
from ulserver.simdev import SimDevice

DEV = SERVER / ".dev"
AGENT = SERVER.parent / "bridges" / "openrgb"
BOARD = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())


def _broker_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 8883), 1).close()
        return (DEV / "pki" / "device-ca.key").exists() and (DEV / "tls" / "tls-ca.crt").exists()
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _broker_up(), reason="dev broker not running (server/tools/dev.sh up)")


async def until(cond, timeout=5.0, what="condition"):
    end = asyncio.get_running_loop().time() + timeout
    while True:
        r = cond()
        if asyncio.iscoroutine(r):
            r = await r
        if r:
            return r
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


def write_identity(tmp_path, pki: DevicePKI, cn: str):
    k, c = pki.issue_client(cn)
    (tmp_path / f"{cn}.key").write_bytes(k)
    (tmp_path / f"{cn}.crt").write_text(cert_pem(c) + pki.chain_pem())
    return tmp_path / f"{cn}.crt", tmp_path / f"{cn}.key"


async def test_bridge_end_to_end(migrated_db, tmp_path):
    s = Settings(database_url=get_settings().database_url, mqtt_host="127.0.0.1", mqtt_port=8883,
                 mqtt_check_hostname=False, mqtt_ca_file=str(DEV / "tls" / "tls-ca.crt"), pki_dir=DEV / "pki")
    devpki = DevicePKI(DEV / "pki")
    server_ca = (DEV / "tls" / "tls-ca.crt").read_text()
    dev = f"ul-{secrets.token_hex(3)}"
    sm = dbm.sessionmaker()
    async with sm() as db:
        db.add(Node(device_id=dev, name="Integration", chip="esp32c3", board=BOARD))
        await db.commit()

    hub = Hub()
    seen: asyncio.Queue = asyncio.Queue()
    hub.add(Client(ws=None, access=Access(1, admin=True), queue=seen))  # type: ignore[arg-type]
    bridge = Bridge(s, hub, sm)
    control = Control(bridge.publish, sm, live_interval=0.02)
    control.cmd_waiters = bridge.cmd_waiters
    bridge.control = control
    btask = asyncio.create_task(bridge.run())
    await asyncio.wait_for(bridge.connected.wait(), 5)

    cert, key = write_identity(tmp_path, devpki, dev)
    sim = SimDevice(dev, BOARD, cert, key, host="ul-dev.lan", server_ca=server_ca, connect_ip="127.0.0.1")
    stop = asyncio.Event()
    stask = asyncio.create_task(sim.run(stop))
    try:
        await asyncio.wait_for(sim.connected.wait(), 5)

        # info + status reach the database; outputs appear from the default mapping.
        async def node_online():
            async with sm() as db:
                n = (await db.execute(Node.__table__.select().where(Node.device_id == dev))).first()
                return n is not None and n.online and n.info is not None
        await until(node_online, what="node online with info")
        await until(lambda: (dev, "warm") in control.outputs, what="outputs cached")

        # set: retained, applied by the device, echoed as state into the DB and to browsers.
        p = await control.set(dev, "warm", {"level": 0.5}, "ui:1")
        await until(lambda: sim.state["warm"].get("level") == 0.5 and sim.state["warm"]["on"], what="device applied set")

        async def actual_level():
            async with sm() as db:
                o = (await db.execute(Output.__table__.select().where(Output.key == "warm"))).all()
                return any(r.actual and r.actual.get("seq") == p["seq"] for r in o)
        await until(actual_level, what="state stored")

        # live: coalesced, QoS 0, applied.
        for lvl in (0.1, 0.2, 0.3, 0.4):
            control.live(dev, "strip", {"color": [255, 0, 0], "level": lvl, "on": True}, "ui:1")
            await asyncio.sleep(0.005)
        await until(lambda: sim.state["strip"].get("level") == 0.4 and sim.state["strip"].get("color") == [255, 0, 0],
                    what="live applied")
        lives = [d for t, d in sim.received if "/live/" in t]
        assert 1 <= len(lives) <= 4

        # Commands round-trip through evt cmd_result.
        r = await control.cmd(dev, "identify", {"ms": 1000}, "admin:1", wait=3)
        assert r["result"] and r["result"]["ok"] is True

        # Config: regroup warm+cool into one CCT output; device re-publishes info; outputs follow.
        cfg = json.loads((CONTRACTS / "examples/config/c3-bench-cct.json").read_text())
        async with sm() as db:
            n = (await db.execute(Node.__table__.select().where(Node.device_id == dev))).first()
        node = Node(id=n.id, device_id=dev, home_id=None, config=cfg, config_rev=cfg["rev"], board=BOARD, chip="esp32c3", name="x")
        await control.push_config(node)
        await until(lambda: (dev, "main") in control.outputs and (dev, "warm") not in control.outputs, what="config applied")
        await control.set(dev, "main", {"cct": 3000, "level": 0.7}, "ui:1")
        await until(lambda: sim.state["main"].get("cct") == 3000, what="cct applied")

        # ACL: another device can't speak for this one.
        c2, k2 = write_identity(tmp_path, devpki, "ul-abcdef")
        ctx = tls_context(s)
        ctx.load_cert_chain(c2, k2)
        async with aiomqtt.Client("127.0.0.1", 8883, identifier="ul-abcdef", tls_context=ctx) as other:
            await other.publish(f"ul/v1/d/{dev}/status", json.dumps({"online": False}), qos=1, retain=True)
        await asyncio.sleep(0.3)
        assert await node_online(), "ACL let a different device publish this device's status"

        # A certificate from another CA is refused by the broker.
        dev_tls = init_dev_tls(tmp_path / "rogue", ["rogue"])
        rogue = tls_context(s)
        k = new_key()
        (tmp_path / "rogue.key").write_bytes(key_pem(k))
        rogue.load_cert_chain(dev_tls["cert"], dev_tls["key"])
        with pytest.raises(aiomqtt.MqttError):
            async with aiomqtt.Client("127.0.0.1", 8883, identifier="rogue", tls_context=rogue, timeout=3):
                pass

        # Hub saw device, state, health and evt messages.
        kinds = set()
        while not seen.empty():
            kinds.add(json.loads(seen.get_nowait())["t"])
        assert {"device", "state", "health", "evt"} <= kinds
    finally:
        stop.set()
        await asyncio.wait_for(stask, 5)
        # Clear every retained topic this test created.
        for t in ["config", "status", "info"] + [f"set/{k}" for k in ("warm", "cool", "strip", "relay", "main", "fan")] + \
                 [f"state/{k}" for k in ("warm", "cool", "strip", "relay", "main", "fan")]:
            await bridge.publish(f"ul/v1/d/{dev}/{t}", None, schema=None, qos=1, retain=True)
        bridge.stop()
        btask.cancel()
        await asyncio.gather(btask, return_exceptions=True)
        await dbm.dispose()


async def test_openrgb_agent_end_to_end(migrated_db, tmp_path):
    """The real OpenRGB agent (simulated OpenRGB servers) against the bridge and the dev broker."""
    import sys
    sys.path.insert(0, str(AGENT))
    from ul_openrgb import config as agent_config
    from ul_openrgb.agent import Agent
    from ul_openrgb.sources import SimBackend

    s = Settings(database_url=get_settings().database_url, mqtt_host="127.0.0.1", mqtt_port=8883,
                 mqtt_check_hostname=False, mqtt_ca_file=str(DEV / "tls" / "tls-ca.crt"), pki_dir=DEV / "pki")
    devpki = DevicePKI(DEV / "pki")
    dev = f"ul-{secrets.token_hex(3)}"
    sm = dbm.sessionmaker()
    async with sm() as db:
        db.add(Node(device_id=dev, name="Desktop", kind="bridge", bridge="openrgb", chip=None, board=None))
        await db.commit()
    hub = Hub()
    bridge = Bridge(s, hub, sm)
    control = Control(bridge.publish, sm, live_interval=0.02)
    control.cmd_waiters = bridge.cmd_waiters
    bridge.control = control
    btask = asyncio.create_task(bridge.run())
    await asyncio.wait_for(bridge.connected.wait(), 5)

    state = tmp_path / "agent"
    state.mkdir()
    cert, key = write_identity(state, devpki, dev)
    cert.rename(state / "device.crt")
    key.rename(state / "device.key")
    cfg = agent_config.parse({
        "server": "ul-dev.lan", "connect": "127.0.0.1", "device_id": dev, "server_ca": str(DEV / "tls" / "tls-ca.crt"),
        "state_dir": str(state),
        "sources": {"pve": {"host": "sim", "name": "Proxmox host"}, "vm": {"host": "sim", "name": "GPU VM"}},
        "parts": {"gpu": {"name": "Graphics card", "source": "vm", "match": ["type:gpu"]},
                  "ram": {"name": "Memory", "source": "pve", "match": ["type:dram"]},
                  "fans": {"name": "Fans", "source": "pve", "match": ["name:Lighting Node"]}}})
    pve, vm = SimBackend(), SimBackend()
    agent = Agent(cfg, backends={"pve": pve, "vm": vm})
    atask = asyncio.create_task(agent.run())

    async def outputs():
        async with sm() as db:
            n = (await db.execute(Node.__table__.select().where(Node.device_id == dev))).first()
            rows = (await db.execute(Output.__table__.select().where(Output.node_id == n.id))).all()
            return n, {r.key: r for r in rows}
    try:
        async def reachable():
            n, o = await outputs()
            return n.online and set(o) == {"gpu", "ram", "fans"} and all(r.meta.get("reachable") for r in o.values())
        await until(reachable, 8, "agent online with all parts reachable")
        n, o = await outputs()
        assert n.info["combine"] is True and o["fans"].meta["part"] == "fans" and o["ram"].kind == "rgb"
        assert o["gpu"].name == "Graphics card"

        # set: each part is its own output with its own seq; the agent draws it on the right source.
        await control.set(dev, "ram", {"on": True, "level": 1.0, "color": [255, 0, 0], "fade_ms": 0}, "ui:1")
        await until(lambda: pve.colors.get("Sim DRAM") == (255, 0, 0), what="ram drawn")
        await control.set(dev, "gpu", {"on": True, "level": 1.0, "color": [0, 0, 255], "fade_ms": 0}, "ui:1")
        await until(lambda: vm.colors.get("Sim GPU") == (0, 0, 255), what="gpu drawn")
        assert pve.colors.get("Sim GPU") is None, "the GPU on the other source is not part of any output there"

        async def actual(key, **want):
            _, o = await outputs()
            a = o[key].actual or {}
            return all(a.get(k) == v for k, v in want.items())
        await until(lambda: actual("gpu", color=[0, 0, 255], on=True), what="gpu state stored")

        # live: partial updates stream; the final state lands.
        for lvl in (0.2, 0.4, 0.6):
            control.live(dev, "ram", {"level": lvl}, "ui:1")
            await asyncio.sleep(0.03)
        await until(lambda: actual("ram", level=0.6), what="live level reported")

        # The VM goes down: only the GPU becomes unreachable, and gets its state back on return.
        vm.up = False
        await control.set(dev, "gpu", {"color": [0, 255, 0]}, "ui:1")

        async def gpu_reach(v):
            _, o = await outputs()
            return o["gpu"].meta.get("reachable") is v and o["ram"].meta.get("reachable") is True
        await until(lambda: gpu_reach(False), 6, "gpu unreachable")
        vm.colors.clear()
        vm.up = True
        await until(lambda: gpu_reach(True), 8, "gpu back")
        await until(lambda: vm.colors.get("Sim GPU") == (0, 255, 0), what="gpu re-applied after its source returned")

        # Commands: identify works, a node-only op is refused.
        r = await control.cmd(dev, "identify", {"ms": 1000, "out": "fans"}, "admin:1", wait=3)
        assert r["result"]["ok"] is True
        r = await control.cmd(dev, "wifi_reset", {}, "admin:1", wait=3)
        assert r["result"] == {"type": "cmd_result", "cmd_id": r["id"], "ok": False, "error": "unsupported"}
        assert not bridge.contract_errors.get(dev), bridge.contract_errors.get(dev)
    finally:
        agent.stop()
        await asyncio.wait_for(atask, 5)
        for t in ["status", "info"] + [f"set/{k}" for k in ("gpu", "ram", "fans")] + [f"state/{k}" for k in ("gpu", "ram", "fans")]:
            await bridge.publish(f"ul/v1/d/{dev}/{t}", None, schema=None, qos=1, retain=True)
        bridge.stop()
        btask.cancel()
        await asyncio.gather(btask, return_exceptions=True)
        await dbm.dispose()


def _image(version: str, size: int = 4000) -> bytes:
    """A minimal ESP-IDF app image header: image header, segment header, esp_app_desc_t with the version."""
    import struct
    head = bytes([0xE9]) + b"\0" * 23 + b"\0" * 8
    desc = struct.pack("<II8x", 0xABCD5432, 0) + version.encode().ljust(32, b"\0")
    return (head + desc).ljust(size, b"\x5a")


async def test_ota_end_to_end(migrated_db, tmp_path):
    """Push a package to a simulated node: progress, restart, back on the new version; a tampered
    download is refused by the node and the update fails."""
    import hashlib

    from sqlalchemy import select

    from ulserver import ota
    from ulserver.models import FirmwarePackage, OtaUpdate

    s = Settings(database_url=get_settings().database_url, mqtt_host="127.0.0.1", mqtt_port=8883,
                 mqtt_check_hostname=False, mqtt_ca_file=str(DEV / "tls" / "tls-ca.crt"), pki_dir=DEV / "pki")
    devpki = DevicePKI(DEV / "pki")
    dev = f"ul-{secrets.token_hex(3)}"
    ver, ver2 = f"8.{secrets.randbelow(10**6)}.0", f"8.{secrets.randbelow(10**6)}.1"
    images = {ver: _image(ver), ver2: _image(ver2)}
    sm = dbm.sessionmaker()
    async with sm() as db:
        db.add(Node(device_id=dev, name="OTA", chip="esp32c3", board=BOARD))
        pkgs = []
        for v, img in images.items():
            m = json.loads((CONTRACTS / "examples/firmware-manifest/c3-lights.json").read_text())
            p = FirmwarePackage(profile="c3-lights", chip="esp32c3", version=v, manifest={**m, "version": v},
                                app_sha256=hashlib.sha256(img).hexdigest())
            db.add(p)
            pkgs.append(p)
        await db.commit()
        pids = [p.id for p in pkgs]

    bridge = Bridge(s, Hub(), sm)
    control = Control(bridge.publish, sm)
    control.cmd_waiters = bridge.cmd_waiters
    bridge.control = control
    btask = asyncio.create_task(bridge.run())
    await asyncio.wait_for(bridge.connected.wait(), 5)

    served = {hashlib.sha256(i).hexdigest(): i for i in images.values()}
    tamper = {"on": False}

    async def fetch(url: str) -> bytes:
        img = served[url.rsplit("/", 1)[1].removesuffix(".bin")]
        return img[:-1] + b"X" if tamper["on"] else img

    cert, key = write_identity(tmp_path, devpki, dev)
    sim = SimDevice(dev, BOARD, cert, key, host="ul-dev.lan", server_ca=(DEV / "tls" / "tls-ca.crt").read_text(),
                    connect_ip="127.0.0.1", fw="0.1.0", profile="c3-lights", fetch=fetch)
    stop = asyncio.Event()
    stask = asyncio.create_task(sim.run(stop))

    async def update(uid):
        async with sm() as db:
            return await db.get(OtaUpdate, uid)

    async def push(pid):
        async with sm() as db:
            n = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one()
            return (await ota.start(db, control, s, n, await db.get(FirmwarePackage, pid), "admin:1")).id

    async def online_on(v):
        async with sm() as db:
            n = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one()
            return n.online and (n.info or {}).get("fw") == v

    try:
        await asyncio.wait_for(sim.connected.wait(), 5)
        await until(lambda: online_on("0.1.0"), what="node online on 0.1.0")
        uid = await push(pids[0])
        u = await until(lambda: _status(update, uid, "done"), timeout=8, what="update done")
        assert u.progress == 100 and u.from_version == "0.1.0" and sim.fw == ver
        assert await online_on(ver)

        tamper["on"] = True
        uid = await push(pids[1])
        u = await until(lambda: _status(update, uid, "failed"), timeout=8, what="tampered update failed")
        assert u.error == "sha256_mismatch" and sim.fw == ver
    finally:
        stop.set()
        await asyncio.wait_for(stask, 5)
        for t in ["status", "info"] + [f"state/{k}" for k in ("warm", "cool", "strip", "relay")]:
            await bridge.publish(f"ul/v1/d/{dev}/{t}", None, schema=None, qos=1, retain=True)
        bridge.stop()
        btask.cancel()
        await asyncio.gather(btask, return_exceptions=True)
        await dbm.dispose()


async def _status(get, uid, want):
    u = await get(uid)
    return u if u.status == want else None
