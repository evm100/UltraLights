"""ulctl: server management from the command line.

    ulctl db upgrade                 apply database migrations
    ulctl create-admin USERNAME      create (or reset the password of) an admin; prompts for the password
    ulctl ca init                    create root + device CA, CRL and the bridge certificate (idempotent)
    ulctl ca status                  show what exists and whether the root key is still online
    ulctl ca export-root DEST        copy root-ca.key to DEST (e.g. a USB stick)
    ulctl ca remove-root-key --yes   delete root-ca.key from the server after exporting it
    ulctl ca crl                     rewrite crl.pem from the database
    ulctl issue-client CN OUTDIR     key + cert from the device CA (tests, simulated devices, bridges)
    ulctl dev-tls DIR HOST...        dev-only TLS CA + server certificate for HOSTs
    ulctl seed                       add the built-in board templates (PCB rev A) if missing

Settings come from UL_* environment variables or the file named by UL_ENV_FILE.
"""
import argparse
import asyncio
import getpass
import json
import os
import shutil
import sys
from pathlib import Path

from .config import get_settings
from .pki import DevicePKI, cert_pem, init_dev_tls

SERVER_DIR = Path(__file__).resolve().parents[1]


def _db_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(cfg, "head")


async def _create_admin(username: str, password: str) -> str:
    from sqlalchemy import select

    from . import db as dbm
    from .models import AuditLog, User
    from .security import hash_password

    async with dbm.sessionmaker()() as db:
        u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if u is None:
            db.add(User(username=username, password_hash=hash_password(password), role="admin", display_name=username))
            action = "created"
        else:
            u.password_hash, u.role, u.active = hash_password(password), "admin", True
            action = "updated"
        db.add(AuditLog(src="cli", action=f"admin_{action}", target=username))
        await db.commit()
    await dbm.dispose()
    return action


async def _seed() -> list[str]:
    from sqlalchemy import select

    from . import db as dbm
    from .models import BoardTemplate

    s = get_settings()
    added = []
    pcb = json.loads((s.contracts_dir / "examples" / "board" / "pcb-rev-a.json").read_text())
    async with dbm.sessionmaker()() as db:
        if (await db.execute(select(BoardTemplate).where(BoardTemplate.name == "PCB rev A"))).scalar_one_or_none() is None:
            db.add(BoardTemplate(name="PCB rev A", board=pcb))
            added.append("PCB rev A")
        await db.commit()
    await dbm.dispose()
    return added


async def _crl() -> None:
    from . import db as dbm
    from .enroll import write_crl

    async with dbm.sessionmaker()() as db:
        await write_crl(db, DevicePKI(get_settings().pki_dir))
    await dbm.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ulctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    db = sub.add_parser("db")
    db.add_argument("action", choices=["upgrade"])
    ca = sub.add_parser("create-admin")
    ca.add_argument("username")
    ca.add_argument("--password-stdin", action="store_true", help="read the password from stdin (scripts)")
    c = sub.add_parser("ca")
    c.add_argument("action", choices=["init", "status", "export-root", "remove-root-key", "crl"])
    c.add_argument("dest", nargs="?")
    c.add_argument("--yes", action="store_true")
    ic = sub.add_parser("issue-client")
    ic.add_argument("cn")
    ic.add_argument("outdir")
    dt = sub.add_parser("dev-tls")
    dt.add_argument("dir")
    dt.add_argument("hosts", nargs="+")
    sub.add_parser("seed")
    a = ap.parse_args(argv)
    s = get_settings()
    pki = DevicePKI(s.pki_dir)

    if a.cmd == "db":
        _db_upgrade()
        print("database is at the latest migration")
    elif a.cmd == "create-admin":
        name = a.username.strip().lower()
        if a.password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass(f"Password for {name}: ")
            if pw != getpass.getpass("Again: "):
                print("passwords differ", file=sys.stderr)
                return 1
        if len(pw) < 10:
            print("use at least 10 characters", file=sys.stderr)
            return 1
        print(f"admin {name} {asyncio.run(_create_admin(name, pw))}")
    elif a.cmd == "ca":
        if a.action == "init":
            made = pki.init()
            if pki.ensure_bridge_cert():
                made.append("ul-server.crt/.key")
            print("created: " + (", ".join(made) if made else "nothing (already initialized)"))
            if pki.p("root-ca.key").exists():
                print("NOTE: root-ca.key is still on this machine. Export it (`ulctl ca export-root /media/usb`) "
                      "and remove it (`ulctl ca remove-root-key --yes`). It is only needed to create a new device CA.")
        elif a.action == "status":
            for f in ["root-ca.crt", "root-ca.key", "device-ca.crt", "device-ca.key", "ca-chain.crt", "crl.pem",
                      "ul-server.crt", "ul-server.key"]:
                print(f"{f:16} {'present' if pki.p(f).exists() else 'missing'}")
        elif a.action == "export-root":
            if not a.dest:
                print("usage: ulctl ca export-root DEST", file=sys.stderr)
                return 2
            dest = Path(a.dest)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pki.p("root-ca.key"), dest / "root-ca.key")
            shutil.copy2(pki.p("root-ca.crt"), dest / "root-ca.crt")
            print(f"copied root-ca.key and root-ca.crt to {dest}. Check the copy, then run `ulctl ca remove-root-key --yes`.")
        elif a.action == "remove-root-key":
            if not a.yes:
                print("this deletes root-ca.key from the server; pass --yes after exporting it", file=sys.stderr)
                return 2
            pki.p("root-ca.key").unlink(missing_ok=True)
            print("root-ca.key removed")
        elif a.action == "crl":
            asyncio.run(_crl())
            print(f"wrote {pki.p('crl.pem')}")
    elif a.cmd == "issue-client":
        out = Path(a.outdir)
        out.mkdir(parents=True, exist_ok=True)
        k, cert = pki.issue_client(a.cn)
        fd = os.open(out / f"{a.cn}.key", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        (out / f"{a.cn}.crt").write_text(cert_pem(cert) + pki.chain_pem())
        print(f"wrote {out / a.cn}.key and .crt")
    elif a.cmd == "dev-tls":
        paths = init_dev_tls(Path(a.dir), a.hosts)
        print(" ".join(f"{k}={v}" for k, v in paths.items()))
    elif a.cmd == "seed":
        added = asyncio.run(_seed())
        print("added templates: " + (", ".join(added) if added else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
