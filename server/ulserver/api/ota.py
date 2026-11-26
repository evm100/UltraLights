"""OTA: the device-facing image download and the admin endpoints that push and follow updates."""
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import firmware as fwm
from .. import ota
from ..db import get_db
from ..models import FirmwarePackage, Home, Node, OtaUpdate
from ..security import Auth, require_admin, src_for

# Devices fetch images here with plain HTTPS (no client certificate). The URL is the image's
# sha256, which only admins and the node being updated are given; images carry no secrets.
device_router = APIRouter(prefix="/api/v1/fw")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@device_router.get("/{sha256}.bin", include_in_schema=False)
async def image(request: Request, sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
                db: AsyncSession = Depends(get_db)) -> FileResponse:
    p = (await db.execute(select(FirmwarePackage).where(FirmwarePackage.app_sha256 == sha256)
                          .order_by(FirmwarePackage.id.desc()).limit(1))).scalar_one_or_none()
    path = fwm.file_path(request.app.state.settings.firmware_dir, p.manifest, p.manifest["app"]) if p else None
    if path is None:
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def _pkg(db: AsyncSession, pid: int) -> FirmwarePackage:
    p = await db.get(FirmwarePackage, pid)
    if p is None:
        raise HTTPException(404, "no such package")
    return p


@router.get("/firmware/{pid}/targets")
async def targets(pid: int, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Nodes that could take this package: every board node, with why not when it can't."""
    p = await _pkg(db, pid)
    await ota.sweep(db)
    await db.commit()
    homes = dict((await db.execute(select(Home.id, Home.name))).all())
    active = set((await db.execute(select(OtaUpdate.node_id).where(OtaUpdate.status.in_(ota.ACTIVE)))).scalars())
    out = []
    for n in (await db.execute(select(Node).where(Node.kind == "node").order_by(Node.name))).scalars():
        info = n.info or {}
        if info and info.get("profile") != p.profile:
            continue  # other profiles aren't candidates at all
        if not info and n.chip != p.chip:
            continue
        reason = None
        try:
            ota.check(n, p)
            if n.id in active:
                reason = "an update is already running"
        except ota.OtaError as e:
            reason = e.detail
        out.append({"dev": n.device_id, "name": n.name, "home": homes.get(n.home_id), "online": n.online,
                    "fw": info.get("fw"), "profile": info.get("profile"), "eligible": reason is None, "reason": reason,
                    "same_version": info.get("fw") == p.version})
    return out


class PushIn(BaseModel):
    package_id: int
    devices: list[str] = Field(min_length=1, max_length=200)
    force: bool = False  # reinstall even if the node already runs this version


@router.post("/ota")
async def push(body: PushIn, request: Request, auth: Auth = Depends(require_admin),
               db: AsyncSession = Depends(get_db)) -> dict:
    p = await _pkg(db, body.package_id)
    src = src_for(auth, "admin")
    results = []
    for dev in dict.fromkeys(body.devices):
        n = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one_or_none()
        if n is None:
            results.append({"dev": dev, "ok": False, "error": "no_such_node", "detail": "no such node"})
            continue
        try:
            u = await ota.start(db, request.app.state.control, request.app.state.settings, n, p,
                                src, force=body.force)
            results.append({"dev": dev, "ok": u.status != "failed", "update": ota.view(u, n),
                            **({"error": "not_sent", "detail": u.error} if u.status == "failed" else {})})
        except ota.OtaError as e:  # raised before anything is written
            results.append({"dev": dev, "ok": False, "error": e.code, "detail": e.detail})
    await db.commit()  # sweeps
    return {"results": results}


@router.get("/ota")
async def updates(dev: str | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    await ota.sweep(db)
    await db.commit()
    q = select(OtaUpdate, Node).join(Node, Node.id == OtaUpdate.node_id).order_by(OtaUpdate.id.desc()).limit(100)
    if dev:
        q = q.where(Node.device_id == dev)
    rows = (await db.execute(q)).all()
    return {"updates": [ota.view(u, n) for u, n in rows], "active": sum(1 for u, _ in rows if u.status in ota.ACTIVE)}
