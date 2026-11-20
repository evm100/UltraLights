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
from ulserver.bridge import Bridge, tls_context
from ulserver.config import Settings, get_settings
from ulserver.control import Control
from ulserver.hub import Client, Hub
from ulserver.models import Node, Output
from ulserver.pki import DevicePKI, cert_pem, init_dev_tls, key_pem, new_key
from ulserver.simdev import SimDevice

DEV = SERVER / ".dev"
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
    hub.add(Client(ws=None, user_id=1, admin=True, homes=None, queue=seen))  # type: ignore[arg-type]
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
