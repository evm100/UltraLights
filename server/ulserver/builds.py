"""Build jobs and workers: the queue lives in the database and workers poll it over HTTPS.

A job is queued by an admin, claimed by one build worker (row lock, SKIP LOCKED), kept alive
by the worker's reports, and ends succeeded (package uploaded), failed or cancelled. A running
job whose worker goes quiet for STALE is failed, so the queue never sticks.
"""
import datetime as dt
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BuildJob, Worker
from .security import sha256

UTC = dt.timezone.utc
STALE = dt.timedelta(minutes=3)        # no report for this long: the worker is gone
ONLINE = dt.timedelta(seconds=90)      # idle workers claim every ~10 s
LOG_LIMIT = 1_000_000                  # characters kept per job (the tail)
TOKEN_PREFIX = "ulw_"
DONE = ("succeeded", "failed", "cancelled")


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def new_token() -> tuple[str, bytes]:
    t = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return t, sha256(t)


def profiles(d: Path) -> list[str]:
    return sorted(p.name.removesuffix(".defaults") for p in d.glob("*.defaults")) if d.is_dir() else []


def append_log(job: BuildJob, text: str) -> None:
    log = (job.log or "") + text
    if len(log) > LOG_LIMIT:
        cut = log[-LOG_LIMIT:]
        nl = cut.find("\n")
        log = "[earlier output trimmed]\n" + (cut[nl + 1:] if 0 <= nl < 1000 else cut)
    job.log = log


def note(job: BuildJob, text: str) -> None:
    """A server marker line ("-- ..."), always on a line of its own."""
    lead = "\n" if job.log and not job.log.endswith("\n") else ""
    append_log(job, f"{lead}-- {text}\n")


def finish(job: BuildJob, status: str, error: str | None = None) -> None:
    job.status, job.finished_at = status, now()
    if error:
        job.error = error[:500]


async def sweep_stale(db: AsyncSession) -> None:
    cutoff = now() - STALE
    await db.execute(update(BuildJob)
                     .where(BuildJob.status == "running", BuildJob.heartbeat_at < cutoff)
                     .values(status="failed", finished_at=now(), error="worker stopped responding"))


async def claim(db: AsyncSession, worker: Worker) -> BuildJob | None:
    if "build" not in (worker.kinds or []):
        return None
    await sweep_stale(db)
    job = (await db.execute(select(BuildJob).where(BuildJob.status == "queued").order_by(BuildJob.id)
                            .limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    job.status, job.worker_id = "running", worker.id
    job.started_at = job.heartbeat_at = now()
    return job


def job_doc(job: BuildJob) -> dict[str, Any]:
    return {"v": 1, "type": "job", "id": job.id, "kind": "build", "profile": job.profile, "ref": job.ref}


def worker_online(w: Worker) -> bool:
    return w.last_seen_at is not None and now() - w.last_seen_at < ONLINE


def worker_view(w: Worker) -> dict[str, Any]:
    return {"id": w.id, "name": w.name, "kinds": w.kinds, "created_at": w.created_at.isoformat(),
            "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None, "last_ip": w.last_ip,
            "online": worker_online(w)}


def job_view(j: BuildJob, *, log: bool = False, worker: str | None = None) -> dict[str, Any]:
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    d = {"id": j.id, "profile": j.profile, "ref": j.ref, "replace": j.replace, "status": j.status,
         "cancel_requested": j.cancel_requested, "worker": worker, "error": j.error, "commit": j.commit,
         "version": j.version, "package_id": j.package_id, "created_by": j.created_by,
         "created_at": iso(j.created_at), "started_at": iso(j.started_at), "heartbeat_at": iso(j.heartbeat_at),
         "finished_at": iso(j.finished_at)}
    if log:
        d["log"] = j.log
    return d
