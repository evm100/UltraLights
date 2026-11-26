"""Worker API (contracts/schemas/worker): workers on other machines claim jobs, report
progress and upload results. Bearer worker token, no cookies, so no CSRF."""
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import builds, contracts
from .. import firmware as fwm
from ..db import get_db
from ..models import AuditLog, BuildJob, FirmwarePackage, Worker
from ..security import sha256

router = APIRouter(prefix="/api/worker")


async def require_worker(request: Request, db: AsyncSession = Depends(get_db)) -> Worker:
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h[:7].lower() == "bearer " else ""
    w = None
    if token.startswith(builds.TOKEN_PREFIX):
        w = (await db.execute(select(Worker).where(Worker.token_hash == sha256(token)))).scalar_one_or_none()
    if w is None:
        raise HTTPException(401, "worker token required", headers={"WWW-Authenticate": "Bearer"})
    w.last_seen_at = builds.now()
    w.last_ip = (request.headers.get("x-real-ip") or (request.client.host if request.client else ""))[:64] or None
    return w


async def _own_job(db: AsyncSession, jid: int, w: Worker) -> BuildJob:
    j = await db.get(BuildJob, jid, with_for_update=True)
    if j is None or j.worker_id != w.id:
        raise HTTPException(404, "no such job")
    return j


def _ack(cancel: bool) -> dict[str, Any]:
    return contracts.validate("worker", {"v": 1, "type": "ack", "cancel": cancel})


@router.get("/me")
async def me(w: Worker = Depends(require_worker), db: AsyncSession = Depends(get_db)) -> dict:
    await db.commit()
    return {"name": w.name, "kinds": w.kinds}


@router.post("/claim")
async def claim(w: Worker = Depends(require_worker), db: AsyncSession = Depends(get_db)):
    job = await builds.claim(db, w)
    doc = builds.job_doc(job) if job else None
    if doc:
        contracts.validate("worker", doc)
        builds.note(job, f"claimed by {w.name}")
    await db.commit()
    return doc if doc else Response(status_code=204)


@router.post("/jobs/{jid}/report")
async def report(jid: int, doc: dict = Body(...), w: Worker = Depends(require_worker),
                 db: AsyncSession = Depends(get_db)) -> dict:
    try:
        contracts.validate("worker", doc)
    except contracts.ContractError as e:
        raise HTTPException(422, {"error": "bad_report", "detail": str(e)}) from None
    if doc["type"] != "report":
        raise HTTPException(422, {"error": "bad_report", "detail": "type must be report"})
    j = await _own_job(db, jid, w)
    if j.status != "running":
        await db.commit()
        return _ack(True)
    if doc.get("log"):
        builds.append_log(j, doc["log"])
    j.heartbeat_at = builds.now()
    if doc.get("failed"):
        builds.finish(j, "failed", doc["failed"])
    elif j.cancel_requested:
        builds.note(j, "cancelled by admin")
        builds.finish(j, "cancelled")
    cancel = j.status != "running"
    await db.commit()
    return _ack(cancel)


@router.post("/jobs/{jid}/package")
async def package(jid: int, request: Request, file: UploadFile = File(...), w: Worker = Depends(require_worker),
                  db: AsyncSession = Depends(get_db)) -> dict:
    j = await _own_job(db, jid, w)
    if j.status != "running":
        await db.commit()
        raise HTTPException(409, {"error": "job_not_running", "detail": f"job is {j.status}"})
    data = await file.read(fwm.MAX_ZIP + 1)
    try:
        manifest, files = fwm.read_package(data)
        if manifest["profile"] != j.profile:
            raise fwm.PackageError(f"package is {manifest['profile']}, job asked for {j.profile}")
        if manifest["commit"].endswith("-dirty"):
            raise fwm.PackageError("package was built from a dirty tree")
    except fwm.PackageError as e:
        builds.finish(j, "failed", f"package rejected: {e}")
        await db.commit()
        raise HTTPException(422, {"error": "package_invalid", "detail": str(e)}) from None
    j.commit, j.version = manifest["commit"], manifest["version"]
    old = (await db.execute(select(FirmwarePackage).where(FirmwarePackage.profile == manifest["profile"],
                                                          FirmwarePackage.version == manifest["version"]))).scalar_one_or_none()
    if old is not None and not j.replace and old.manifest.get("commit") != manifest["commit"]:
        msg = (f"{j.profile} {manifest['version']} already exists (commit {old.manifest.get('commit')}); "
               "bump firmware/version.txt or queue the build with replace")
        builds.note(j, msg)
        builds.finish(j, "failed", msg)
        await db.commit()
        raise HTTPException(409, {"error": "version_exists", "detail": msg})
    p = await fwm.save_package(db, request.app.state.settings.firmware_dir, manifest, files, j.created_by)
    j.package_id = p.id
    builds.note(j, f"package {manifest['profile']} {manifest['version']} ({manifest['commit']}) stored")
    builds.finish(j, "succeeded")
    db.add(AuditLog(src=f"worker:{w.id}", action="firmware_built", target=f"{p.profile}@{p.version}",
                    detail={"job": j.id, "commit": manifest["commit"], "ref": j.ref}))
    await db.commit()
    return {"ok": True, "package_id": p.id}
