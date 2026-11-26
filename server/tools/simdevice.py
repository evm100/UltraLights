#!/usr/bin/env python3
"""Simulated node for bench testing the server without hardware.

    # 1. In the admin panel: create a node, then Provision -> "Download blob" (ul_board.bin)
    # 2. Enroll and run:
    server/.venv/bin/python server/tools/simdevice.py ul_board.bin --connect 127.0.0.1

The blob supplies device id, token, server name and (in dev) the server CA, exactly as
a flashed node would read them from its ul_board partition. The certificate and key
are kept in --state (default server/.dev/sim/<device_id>/) so restarts don't re-enroll.
--connect overrides the address used for HTTPS and MQTT when the server name doesn't
resolve on this machine yet; certificate chains are still verified.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ulserver import contracts  # noqa: E402
from ulserver.simdev import SimDevice, enroll, random_mac  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("blob", type=Path, help="ul_board.bin from the node builder")
    ap.add_argument("--connect", help="IP address to connect to instead of resolving the server name")
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--state", type=Path)
    ap.add_argument("--fw", default="0.0.1", help="firmware version to report (default 0.0.1)")
    ap.add_argument("--profile", help="profile to report, e.g. c3-lights to take that profile's OTA images "
                                      "(default <chip>-sim); a successful OTA changes the version until restart")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    payload = contracts.ulboard().decode(a.blob.read_bytes())
    contracts.validate("partition", payload)
    prov = payload["provision"]
    dev = prov["device_id"]
    state = a.state or Path(__file__).resolve().parents[1] / ".dev" / "sim" / dev
    state.mkdir(parents=True, exist_ok=True)
    meta = state / "identity.json"
    if not meta.exists():
        if "token" not in prov:
            print("blob has no token and no saved identity; provision with a token", file=sys.stderr)
            return 1
        ident = enroll(payload, random_mac(), a.connect, a.https_port)
        fd = os.open(state / "device.key", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(ident["key"])
        (state / "device.crt").write_text(ident["cert"] + ident["chain"])
        meta.write_text(json.dumps({"mqtt": ident["mqtt"], "mac": random_mac()}))
        print(f"enrolled {dev}")
    ident = json.loads(meta.read_text())
    sim = SimDevice(device_id=dev, board=payload["board"], cert_file=state / "device.crt", key_file=state / "device.key",
                    host=ident["mqtt"]["host"], port=ident["mqtt"]["port"], server_ca=prov.get("server_ca"),
                    connect_ip=a.connect, board_crc=contracts.ulboard().crc_hex(payload), mac=ident["mac"],
                    fw=a.fw, profile=a.profile, https_port=a.https_port)
    print(f"{dev} connecting to {ident['mqtt']['host']}:{ident['mqtt']['port']} (Ctrl-C to stop)")
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
