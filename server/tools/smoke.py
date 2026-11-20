#!/usr/bin/env python3
"""End-to-end smoke test of a running server, the way the web UI and a node use it.

    server/.venv/bin/python server/tools/smoke.py https://ul-dev.lan --connect 192.168.1.41 \
        --ca server/.dev/tls/tls-ca.crt --user evm

Signs in, creates a node in the builder API, builds its ul_board blob, enrolls a
simulated device with it over HTTPS (through nginx), connects it over mutual TLS,
then drives it from a browser-style WebSocket (live + set) and checks that the
device applied the values and that the browser received the state echoes.
Deletes the node at the end. Password from UL_SMOKE_PASSWORD or a prompt.
"""
import argparse
import asyncio
import base64
import getpass
import json
import os
import ssl
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ulserver import contracts  # noqa: E402
from ulserver.simdev import SimDevice, enroll, random_mac  # noqa: E402

BOARD = json.loads((Path(__file__).resolve().parents[2] / "contracts/examples/board/c3-bench.json").read_text())


def step(msg: str) -> None:
    print(f"  ✓ {msg}", flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--connect", help="IP to connect to instead of resolving the host name")
    ap.add_argument("--ca", help="CA file for the server certificate (dev)")
    ap.add_argument("--user", required=True)
    a = ap.parse_args()
    host = urlparse(a.url).hostname
    pw = os.environ.get("UL_SMOKE_PASSWORD") or getpass.getpass(f"Password for {a.user}: ")
    ctx = ssl.create_default_context(cafile=a.ca) if a.ca else ssl.create_default_context()

    # With --connect, dial the IP but keep SNI, the Host header and the certificate check on the real name.
    base = a.url.replace(host, a.connect) if a.connect else a.url
    ext = {"sni_hostname": host} if a.connect else {}

    class Client(httpx.AsyncClient):
        async def request(self, method, url, **kw):  # type: ignore[override]
            kw["extensions"] = {**(kw.get("extensions") or {}), **ext}
            return await super().request(method, url, **kw)

    async with Client(base_url=base, verify=ctx, timeout=15, headers={"Host": host}) as http:
        r = await http.post("/api/auth/login", json={"username": a.user, "password": pw})
        r.raise_for_status()
        csrf = r.json()["csrf"]
        http.headers["X-UL-CSRF"] = csrf
        step("signed in")
        r = await http.post("/api/admin/nodes", json={"name": "Smoke test node", "board": BOARD})
        r.raise_for_status()
        dev = r.json()["id"]
        step(f"node {dev} created from the builder API")
        try:
            r = await http.post(f"/api/admin/nodes/{dev}/provision", json={"dns": "192.168.1.99"})
            r.raise_for_status()
            payload = contracts.ulboard().decode(base64.b64decode(r.json()["blob_b64"]))
            contracts.validate("partition", payload)
            step(f"ul_board blob {r.json()['crc']} built (server_ca included: {r.json()['server_ca']})")

            ident = await asyncio.to_thread(enroll, payload, random_mac(), a.connect)
            step("simulated device enrolled over HTTPS with its one-time token")
            tmp = Path(tempfile.mkdtemp(prefix="ul-smoke-"))
            (tmp / "d.key").write_text(ident["key"])
            os.chmod(tmp / "d.key", 0o600)
            (tmp / "d.crt").write_text(ident["cert"] + ident["chain"])
            sim = SimDevice(dev, BOARD, tmp / "d.crt", tmp / "d.key", host=ident["mqtt"]["host"], port=ident["mqtt"]["port"],
                            server_ca=payload["provision"].get("server_ca"), connect_ip=a.connect,
                            board_crc=contracts.ulboard().crc_hex(payload))
            stop = asyncio.Event()
            stask = asyncio.create_task(sim.run(stop))
            await asyncio.wait_for(sim.connected.wait(), 10)
            step("device connected to the broker with mutual TLS")

            ws_url = a.url.replace("https://", "wss://") + "/api/ws"
            cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
            extra = {"Cookie": cookie, "Origin": a.url}
            connect_kw = {"ssl": ctx, "additional_headers": extra}
            if a.connect:
                connect_kw["host"] = a.connect
                connect_kw["server_hostname"] = host
            async with websockets.connect(ws_url, **connect_kw) as ws:
                snap = json.loads(await ws.recv())
                contracts.validate("ws-server", snap)
                for _ in range(50):
                    if any(d["id"] == dev and d["outputs"] for d in snap["devices"]):
                        break
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if msg["t"] == "device" and msg["device"]["id"] == dev and msg["device"]["outputs"]:
                        break
                step("browser WebSocket has the device and its outputs")
                for lvl in (0.2, 0.35, 0.5, 0.65):
                    await ws.send(json.dumps({"t": "live", "dev": dev, "out": "warm", "state": {"on": True, "level": lvl}}))
                    await asyncio.sleep(0.02)
                await ws.send(json.dumps({"t": "set", "dev": dev, "out": "warm", "state": {"on": True, "level": 0.7}}))
                await ws.send(json.dumps({"t": "set", "dev": dev, "out": "strip", "state": {"on": True, "color": [0, 80, 255], "level": 0.5}}))
                got = {}
                deadline = asyncio.get_running_loop().time() + 5
                while asyncio.get_running_loop().time() < deadline and not ({"warm", "strip"} <= set(got)):
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    contracts.validate("ws-server", msg)
                    if msg["t"] == "state" and msg["dev"] == dev:
                        if msg["out"] == "warm" and msg["state"].get("level") == 0.7:
                            got["warm"] = msg["state"]
                        if msg["out"] == "strip" and msg["state"].get("color") == [0, 80, 255]:
                            got["strip"] = msg["state"]
                assert sim.state["warm"]["level"] == 0.7 and sim.state["strip"]["color"] == [0, 80, 255], sim.state
                assert {"warm", "strip"} <= set(got), got
                lives = sum(1 for t, _ in sim.received if "/live/warm" in t)
                step(f"live + set applied by the device ({lives} live messages for 4 slider moves) and echoed to the browser")
            r = await http.post(f"/api/admin/nodes/{dev}/cmd", json={"op": "identify", "args": {"ms": 1000}})
            r.raise_for_status()
            assert r.json()["result"]["ok"], r.json()
            step("identify command answered by the device")
            stop.set()
            await asyncio.wait_for(stask, 5)
        finally:
            r = await http.delete(f"/api/admin/nodes/{dev}")
            step(f"node deleted ({r.status_code})")
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
