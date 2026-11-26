"""Admin: build jobs (queue, watch, cancel) and the worker tokens that run them."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import builds, contracts
from ..db import get_db
from ..models import AuditLog, BuildJob, Worker
from ..security import Auth, require_admin, src_for

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _ref_ok(ref: str) -> bool:
    return contracts.is_valid("worker", {"v": 1, "type": "job", "id": 1, "kind": "build",
                                         "profile": "c3-x", "ref": ref})


class BuildIn(BaseModel):
    profile: str
    ref: str = "beta"
    replace: bool = False


@router.get("/build-profiles")
async def build_profiles(request: Request) -> list[str]:
    return builds.profiles(request.app.state.settings.firmware_profiles_dir)


@router.get("/builds")
async def list_builds(db: AsyncSession = Depends(get_db)) -> dict:
    await builds.sweep_stale(db)
    await db.commit()
    names = dict((await db.execute(select(Worker.id, Worker.name))).all())
    rows = (await db.execute(select(BuildJob).order_by(BuildJob.id.desc()).limit(100))).scalars().all()
    workers = (await db.execute(select(Worker).order_by(Worker.name))).scalars().all()
    return {"jobs": [builds.job_view(j, worker=names.get(j.worker_id)) for j in rows],
            "workers": [builds.worker_view(w) for w in workers],
            "queued": sum(1 for j in rows if j.status == "queued")}


@router.post("/builds")
async def queue_build(body: BuildIn, request: Request, auth: Auth = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    if body.profile not in builds.profiles(request.app.state.settings.firmware_profiles_dir):
        raise HTTPException(422, {"error": "unknown_profile", "detail": f"no profile {body.profile}"})
    if not _ref_ok(body.ref):
        raise HTTPException(422, {"error": "bad_ref", "detail": "ref must be a branch, tag or commit"})
    j = BuildJob(profile=body.profile, ref=body.ref, replace=body.replace, created_by=auth.user.id, log="")
    db.add(j)
    await db.flush()
    db.add(AuditLog(src=src_for(auth, "admin"), action="build_queued", target=f"{j.profile}@{j.ref}",
                    detail={"job": j.id, "replace": j.replace}))
    await db.commit()
    return builds.job_view(j)


@router.get("/builds/{jid}")
async def get_build(jid: int, db: AsyncSession = Depends(get_db)) -> dict:
    await builds.sweep_stale(db)
    await db.commit()
    j = await db.get(BuildJob, jid)
    if j is None:
        raise HTTPException(404, "no such build")
    w = await db.get(Worker, j.worker_id) if j.worker_id else None
    return builds.job_view(j, log=True, worker=w.name if w else None)


@router.post("/builds/{jid}/cancel")
async def cancel_build(jid: int, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    j = await db.get(BuildJob, jid, with_for_update=True)
    if j is None:
        raise HTTPException(404, "no such build")
    if j.status == "queued":
        builds.finish(j, "cancelled")
    elif j.status == "running":
        j.cancel_requested = True  # the worker stops at its next report
    db.add(AuditLog(src=src_for(auth, "admin"), action="build_cancelled", target=str(j.id)))
    await db.commit()
    return builds.job_view(j)


class WorkerIn(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    kinds: list[str] = ["build"]


@router.post("/workers")
async def create_worker(body: WorkerIn, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    if not body.kinds or any(k not in ("build",) for k in body.kinds):
        raise HTTPException(422, "kinds must be ['build'] (the AI worker comes later)")
    if (await db.execute(select(Worker).where(Worker.name == body.name))).scalar_one_or_none():
        raise HTTPException(409, "a worker with that name exists")
    token, h = builds.new_token()
    w = Worker(name=body.name, kinds=sorted(set(body.kinds)), token_hash=h)
    db.add(w)
    await db.flush()
    db.add(AuditLog(src=src_for(auth, "admin"), action="worker_created", target=w.name))
    await db.commit()
    return {**builds.worker_view(w), "token": token}


@router.post("/workers/{wid}/token")
async def rotate_worker_token(wid: int, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    w = await db.get(Worker, wid)
    if w is None:
        raise HTTPException(404, "no such worker")
    token, w.token_hash = builds.new_token()
    db.add(AuditLog(src=src_for(auth, "admin"), action="worker_token_rotated", target=w.name))
    await db.commit()
    return {**builds.worker_view(w), "token": token}


@router.delete("/workers/{wid}")
async def delete_worker(wid: int, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    w = await db.get(Worker, wid)
    if w is None:
        raise HTTPException(404, "no such worker")
    await db.execute(update(BuildJob).where(BuildJob.worker_id == wid, BuildJob.status == "running")
                     .values(status="failed", finished_at=builds.now(), error="worker deleted"))
    await db.execute(delete(Worker).where(Worker.id == wid))
    db.add(AuditLog(src=src_for(auth, "admin"), action="worker_deleted", target=w.name))
    await db.commit()
    return {"ok": True}
