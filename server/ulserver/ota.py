"""OTA updates: push a stored package to a node and follow it to the version it comes back with.

The node gets an `ota` cmd (contracts/schemas/cmd) with a URL, the app image's sha256 and its
version. It downloads over plain HTTPS (no client certificate) from /api/v1/fw/<sha256>.bin,
reports progress as cmd_result with `progress`, a final ok cmd_result when the image is written,
then restarts. The update is done when its info reports the new version; a node that comes back
on another version after restarting rolled back (the new image never reached the broker).

    sent -> downloading -> restarting -> done
         \\-------------\\------------\\-> failed (device error, rolled back, timed out, not sent)
"""
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .control import Control, ControlError
from .models import AuditLog, FirmwarePackage, Node, OtaUpdate

UTC = dt.timezone.utc
ACTIVE = ("sent", "downloading", "restarting")
# How long each step may go quiet before the update is failed.
TIMEOUTS = {"sent": dt.timedelta(minutes=2), "downloading": dt.timedelta(minutes=5),
            "restarting": dt.timedelta(minutes=3)}


class OtaError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def device_url(s: Settings, sha256: str) -> str:
    return f"https://{s.public_host}/api/v1/fw/{sha256}.bin"


def _fail(u: OtaUpdate, error: str) -> None:
    u.status, u.error, u.finished_at, u.updated_at = "failed", error[:200], now(), now()


def _done(u: OtaUpdate) -> None:
    u.status, u.progress, u.finished_at, u.updated_at = "done", 100, now(), now()


async def sweep(db: AsyncSession) -> None:
    """Fail updates whose step has gone quiet. Cheap; called before reads and new pushes."""
    t = now()
    rows = (await db.execute(select(OtaUpdate).where(OtaUpdate.status.in_(ACTIVE)))).scalars().all()
    for u in rows:
        if t - u.updated_at > TIMEOUTS[u.status]:
            _fail(u, {"sent": "no answer from the node", "downloading": "download stalled",
                      "restarting": "node didn't come back"}[u.status])


def check(node: Node, pkg: FirmwarePackage, *, force: bool = False) -> None:
    """Why this package can't go to this node now (OtaError), or nothing."""
    if node.kind != "node":
        raise OtaError("not_a_node", f"{node.device_id} is a bridge; bridges update themselves")
    info = node.info or {}
    if not info:
        raise OtaError("no_info", "the node hasn't reported its firmware yet")
    if info.get("profile") != pkg.profile:
        raise OtaError("wrong_profile", f"node runs {info.get('profile')}, package is {pkg.profile}")
    if not node.online:
        raise OtaError("offline", "node is offline")
    if info.get("fw") == pkg.version and not force:
        raise OtaError("same_version", f"node already runs {pkg.version}")
    if not pkg.app_sha256:
        raise OtaError("no_image", "package has no app image")


async def start(db: AsyncSession, control: Control, s: Settings, node: Node, pkg: FirmwarePackage, src: str,
                *, force: bool = False) -> OtaUpdate:
    await sweep(db)
    check(node, pkg, force=force)
    busy = (await db.execute(select(OtaUpdate.id).where(OtaUpdate.node_id == node.id,
                                                        OtaUpdate.status.in_(ACTIVE)))).first()
    if busy:
        raise OtaError("busy", "an update to this node is already running")
    u = OtaUpdate(node_id=node.id, package_id=pkg.id, profile=pkg.profile, from_version=(node.info or {}).get("fw"),
                  to_version=pkg.version, cmd_id=str(uuid.uuid4()), status="sent", progress=0, src=src)
    db.add(u)
    db.add(AuditLog(src=src, action="ota_sent", target=node.device_id,
                    detail={"package": pkg.id, "from": u.from_version, "to": pkg.version}))
    await db.commit()  # the row exists before the node can answer
    from .bridge import NotConnected  # the bridge imports this module

    try:
        await control.cmd(node.device_id, "ota", {"url": device_url(s, pkg.app_sha256), "sha256": pkg.app_sha256,
                                                  "version": pkg.version}, src, cid=u.cmd_id)
    except (ControlError, NotConnected) as e:
        _fail(u, f"not sent: {e}")
        await db.commit()
    return u


async def on_cmd_result(db: AsyncSession, node: Node, doc: dict[str, Any]) -> OtaUpdate | None:
    u = (await db.execute(select(OtaUpdate).where(OtaUpdate.cmd_id == doc["cmd_id"],
                                                  OtaUpdate.node_id == node.id))).scalar_one_or_none()
    if u is None or u.status not in ACTIVE:
        return None
    if not doc["ok"]:
        _fail(u, doc.get("error", "failed") + (f" ({doc['detail']})" if doc.get("detail") else ""))
    elif "progress" in doc:
        u.status, u.progress, u.updated_at = "downloading", max(u.progress, doc["progress"]), now()
    else:
        u.status, u.progress, u.updated_at = "restarting", 100, now()
    return u


async def on_info(db: AsyncSession, node: Node, doc: dict[str, Any]) -> list[OtaUpdate]:
    """A node's info after a connect: did it come back on the new version?"""
    if node.kind != "node":
        return []
    rows = (await db.execute(select(OtaUpdate).where(OtaUpdate.node_id == node.id,
                                                     OtaUpdate.status.in_(ACTIVE)))).scalars().all()
    for u in rows:
        if doc["fw"] == u.to_version and (u.status == "restarting" or u.from_version != u.to_version):
            _done(u)
            db.add(AuditLog(src="device", action="ota_done", target=node.device_id, detail={"version": u.to_version}))
        elif u.status == "restarting":
            _fail(u, f"rolled back: came back on {doc['fw']}")
            db.add(AuditLog(src="device", action="ota_rolled_back", target=node.device_id,
                            detail={"version": u.to_version, "running": doc["fw"]}))
    return list(rows)


def view(u: OtaUpdate, node: Node | None = None) -> dict[str, Any]:
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    return {"id": u.id, "dev": node.device_id if node else None, "name": node.name if node else None,
            "package_id": u.package_id, "profile": u.profile, "from_version": u.from_version,
            "to_version": u.to_version, "status": u.status, "progress": u.progress, "error": u.error, "src": u.src,
            "created_at": iso(u.created_at), "updated_at": iso(u.updated_at), "finished_at": iso(u.finished_at)}
