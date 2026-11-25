"""ul-openrgb: discover | enroll | run | check."""
import argparse
import asyncio
import logging
import signal
import socket
import ssl
import sys
from pathlib import Path

from . import __version__
from . import config as cf
from .sources import make_backend


def _sources_from_args(items: list[str]) -> dict[str, cf.Source]:
    """key=host[:port][,Name] ..."""
    out = {}
    for it in items:
        key, _, rest = it.partition("=")
        addr, _, name = rest.partition(",")
        host, _, port = addr.partition(":")
        if not cf.KEY_RE.match(key) or not host:
            raise SystemExit(f"--source {it!r}: expected key=host[:port][,Name], key like pve or vm")
        out[key] = cf.Source(key=key, host=host, port=int(port or 6742), name=name or key)
    return out


def cmd_discover(a: argparse.Namespace) -> int:
    path = Path(a.config)
    existing = None
    if path.exists():
        try:
            existing = cf.load(path)
        except cf.ConfigError:
            existing = None
    sources = _sources_from_args(a.source) if a.source else (existing.sources if existing else {})
    if not sources:
        print("No sources. Pass --source pve=127.0.0.1 --source vm=192.168.1.41 (one per OpenRGB server).", file=sys.stderr)
        return 2
    parts: dict[str, cf.Part] = {}
    reachable = 0
    for s in sources.values():
        print(f"\n{s.key} ({s.name or s.key}) at {s.address}")
        b = make_backend(s)
        try:
            devices = b.connect()
        except Exception as e:  # noqa: BLE001
            print(f"  ! unreachable: {e}. Is `openrgb --server` running there, and port {s.port} open to this machine?")
            continue
        finally:
            b.close()
        reachable += 1
        for d in devices:
            key, part, name, matcher = cf.suggest_part(d.name, d.type)
            p = parts.get(key)
            if p is None:
                parts[key] = cf.Part(key=key, source=s.key, match=[matcher], name=name, part=part)
            elif p.source == s.key:
                if matcher not in p.match:
                    p.match.append(matcher)
            else:  # same kind of part on another machine: keep them apart
                key = f"{key}_{s.key}"[:16]
                p = parts.setdefault(key, cf.Part(key=key, source=s.key, match=[], name=f"{name} ({s.name or s.key})"[:48], part=part))
                if matcher not in p.match:
                    p.match.append(matcher)
            print(f"  [{d.index}] {d.name}  type={d.type}  leds={d.leds}  -> part {key}")
    if not parts:
        print("\nNo devices found, nothing to suggest.", file=sys.stderr)
        return 1
    c = existing or cf.Config()
    c.sources = sources
    c.parts = parts
    if a.server:
        c.server = a.server
    text = cf.render(c)
    if a.write:
        if path.exists() and not a.force:
            print(f"\n{path} exists; re-run with --force to replace its sources and parts.", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        print(f"\nWrote {path}. Rename parts or merge them there, then restart the agent.")
    else:
        print("\nSuggested config (use --write to save it):\n")
        print(text)
    if reachable < len(sources):
        print("\nSome sources were unreachable, so their parts are missing. Fix them and run discover again.", file=sys.stderr)
    return 0


def cmd_enroll(a: argparse.Namespace) -> int:
    from .enroll import EnrollError, enroll

    path = Path(a.config)
    c = cf.load(path)
    c.server = a.server or c.server
    if not c.server:
        print("--server is required (e.g. lights.evm100.org)", file=sys.stderr)
        return 2
    if not cf.DEVICE_ID_RE.match(a.device):
        print("--device must look like ul-4c2e19 (shown in the admin panel)", file=sys.stderr)
        return 2
    try:
        res = enroll(c.server, a.device, a.token, c.key_file, c.cert_file, server_ca=c.server_ca,
                     connect=c.connect, port=c.https_port)
    except EnrollError as e:
        print(f"Enrollment failed: {e}", file=sys.stderr)
        return 1
    cf.set_device_id(path, c.server, a.device)
    port = res.get("mqtt", {}).get("port", 8883)
    if port != c.mqtt_port:
        print(f"Note: the server's broker listens on {port}; set mqtt_port = {port} in {path}.")
    print(f"Enrolled as {a.device}. Certificate in {c.cert_file}. Start the agent: systemctl restart ul-openrgb")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    c = cf.load(Path(a.config))
    bad = 0
    print(f"config      {a.config}: {len(c.sources)} source(s), {len(c.parts)} part(s)")
    print(f"device      {c.device_id or 'not enrolled'}")
    if not (c.key_file.exists() and c.cert_file.exists()):
        print(f"identity    missing ({c.key_file}); run `ul-openrgb enroll`")
        bad += 1
    for s in c.sources.values():
        b = make_backend(s)
        try:
            devs = b.connect()
            print(f"source      {s.key} {s.address}: {len(devs)} device(s)")
            from .sources import assign
            for k, v in assign(c.parts_on(s.key), devs).items():
                print(f"  part      {k}: {', '.join(d.name for d in v) or 'NO DEVICES MATCH'}")
                bad += not v
        except Exception as e:  # noqa: BLE001
            print(f"source      {s.key} {s.address}: unreachable ({e})")
            bad += 1
        finally:
            b.close()
    host = c.connect or c.server
    try:
        socket.create_connection((host, c.mqtt_port), 5).close()
        print(f"broker      {host}:{c.mqtt_port} reachable")
    except OSError as e:
        print(f"broker      {host}:{c.mqtt_port} unreachable ({e})")
        bad += 1
    if c.cert_file.exists():
        try:
            from .agent import tls_context
            tls_context(c)
        except (OSError, ssl.SSLError) as e:
            print(f"identity    unusable: {e}")
            bad += 1
    return 1 if bad else 0


def cmd_run(a: argparse.Namespace) -> int:
    from .agent import Agent

    c = cf.load(Path(a.config))
    agent = Agent(c)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, agent.stop)
        await agent.run()

    asyncio.run(main())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ul-openrgb", description="UltraLights OpenRGB bridge agent " + __version__)
    p.add_argument("--config", default=str(cf.DEFAULT_CONFIG))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="list OpenRGB devices and suggest parts")
    d.add_argument("--source", action="append", default=[], metavar="KEY=HOST[:PORT][,NAME]")
    d.add_argument("--server", help="UltraLights server name to put in a new config")
    d.add_argument("--write", action="store_true", help="save the suggestion as the config")
    d.add_argument("--force", action="store_true", help="with --write: replace an existing config's sources and parts")
    e = sub.add_parser("enroll", help="get this agent's certificate with a one-time token from the admin panel")
    e.add_argument("--server")
    e.add_argument("--device", required=True)
    e.add_argument("--token", required=True)
    sub.add_parser("check", help="check config, identity, sources and broker")
    sub.add_parser("run", help="run the agent (systemd does this)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("openrgb").setLevel(logging.WARNING)
    try:
        return {"discover": cmd_discover, "enroll": cmd_enroll, "check": cmd_check, "run": cmd_run}[a.cmd](a)
    except cf.ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
