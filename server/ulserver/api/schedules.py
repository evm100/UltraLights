"""Schedules for the people in a home, the home's place (time zone, location), and the
internal endpoint the scheduler service calls when something is due.

The scheduler decides when; this process does the work, so every device write goes through
one Control (its desired-state cache stays right) and is checked the same way.
"""
import datetime as dt
import hmac
import secrets
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import MANAGE, SCENES, AccessDenied
from ..actions import ActionError, check_schedule, home_zone, run_schedule
from ..db import get_db
from ..models import AuditLog, Home, Schedule
from ..schedule_times import next_event
from ..security import Auth, require_auth, src_for

router = APIRouter(prefix="/api")
UTC = dt.timezone.utc
INTERNAL_HEADER = "x-ul-internal"
TOKEN_FILE = "internal.token"


def write_internal_token(data_dir: Path) -> str:
    """A fresh secret per API start, readable only by the service user (the scheduler runs as it)."""
    token = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / TOKEN_FILE
    tmp = path.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(token)
    tmp.replace(path)
    return token


def _require(auth: Auth, hid: int, ability: str) -> None:
    try:
        auth.access.require_home(hid, ability)
    except AccessDenied as e:
        raise HTTPException(403 if e.code == "forbidden" else 404, {"error": e.code, "detail": e.detail}) from None


async def _home(db: AsyncSession, hid: int) -> Home:
    h = await db.get(Home, hid)
    if h is None:
        raise HTTPException(404, {"error": "no_such_home"})
    return h


def _view(s: Schedule, home: Home) -> dict[str, Any]:
    tz = home_zone(home)
    nxt = None
    if s.enabled and tz is not None:
        ev = next_event(s.doc, tz, home.latitude, home.longitude, dt.datetime.now(UTC))
        nxt = {"at": ev[0].isoformat(), "phase": ev[1]} if ev else None
    return {"id": s.id, "home_id": s.home_id, "name": s.name, "enabled": s.enabled, "doc": s.doc, "next": nxt,
            "last_start": s.last_start.isoformat() if s.last_start else None, "last_result": s.last_result}


# ---------------------------------------------------------------- the home's place
class PlaceIn(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


@router.put("/homes/{hid}/place")
async def put_place(hid: int, body: PlaceIn, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    """Time zone (needed for schedules) and, optionally, location (for sunrise and sunset)."""
    _require(auth, hid, MANAGE)
    h = await _home(db, hid)
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, {"error": "invalid", "detail": f"unknown time zone {body.timezone!r}"}) from None
    if (body.latitude is None) != (body.longitude is None):
        raise HTTPException(422, {"error": "invalid", "detail": "give both latitude and longitude, or neither"})
    h.timezone, h.latitude, h.longitude = body.timezone, body.latitude, body.longitude
    db.add(AuditLog(src=src_for(auth), action="home_place_set", target=f"home:{hid}",
                    detail={"timezone": body.timezone, "location": body.latitude is not None}))
    await db.commit()
    return {"ok": True}


@router.get("/homes/{hid}/place")
async def get_place(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    if not auth.access.sees_home(hid):
        raise HTTPException(404, {"error": "no_such_home"})
    h = await _home(db, hid)
    return {"timezone": h.timezone, "latitude": h.latitude, "longitude": h.longitude}


# ---------------------------------------------------------------- schedules
@router.get("/homes/{hid}/schedules")
async def list_schedules(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    if not auth.access.sees_home(hid):
        raise HTTPException(404, {"error": "no_such_home"})
    h = await _home(db, hid)
    rows = (await db.execute(select(Schedule).where(Schedule.home_id == hid).order_by(Schedule.name))).scalars().all()
    return [_view(s, h) for s in rows]


async def _checked(db: AsyncSession, request: Request, home: Home, doc: dict[str, Any]) -> dict[str, Any]:
    try:
        return await check_schedule(db, request.app.state.control, home, doc)
    except ActionError as e:
        raise HTTPException(422, {"error": "schedule_invalid", "errors": e.errors}) from None


def _store(s: Schedule, doc: dict[str, Any]) -> None:
    s.doc, s.name, s.enabled = doc, doc["name"], doc.get("enabled", True)
    s.updated_at = dt.datetime.now(UTC)


@router.post("/homes/{hid}/schedules")
async def create_schedule(hid: int, doc: dict[str, Any], request: Request, auth: Auth = Depends(require_auth),
                          db: AsyncSession = Depends(get_db)) -> dict:
    _require(auth, hid, SCENES)
    h = await _home(db, hid)
    await _checked(db, request, h, doc)
    s = Schedule(home_id=hid, created_by=auth.user.id, name="", doc={})
    _store(s, doc)
    # Don't fire events from before it existed.
    s.last_start = s.last_until = dt.datetime.now(UTC)
    db.add(s)
    db.add(AuditLog(src=src_for(auth), action="schedule_created", target=f"home:{hid}", detail={"name": s.name}))
    await db.commit()
    return _view(s, h)


async def _schedule(db: AsyncSession, auth: Auth, sid: int) -> Schedule:
    s = await db.get(Schedule, sid)
    if s is None or not auth.access.sees_home(s.home_id):
        raise HTTPException(404, {"error": "no_such_schedule"})
    return s


@router.put("/schedules/{sid}")
async def put_schedule(sid: int, doc: dict[str, Any], request: Request, auth: Auth = Depends(require_auth),
                       db: AsyncSession = Depends(get_db)) -> dict:
    s = await _schedule(db, auth, sid)
    _require(auth, s.home_id, SCENES)
    h = await _home(db, s.home_id)
    await _checked(db, request, h, doc)
    _store(s, doc)
    s.last_start = s.last_until = dt.datetime.now(UTC)  # a changed time never fires retroactively
    db.add(AuditLog(src=src_for(auth), action="schedule_changed", target=f"schedule:{sid}", detail={"name": s.name}))
    await db.commit()
    return _view(s, h)


@router.delete("/schedules/{sid}")
async def delete_schedule(sid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    s = await _schedule(db, auth, sid)
    _require(auth, s.home_id, SCENES)
    await db.delete(s)
    db.add(AuditLog(src=src_for(auth), action="schedule_deleted", target=f"schedule:{sid}", detail={"name": s.name}))
    await db.commit()
    return {"ok": True}


@router.post("/schedules/{sid}/run")
async def run_now(sid: int, request: Request, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    """Try it: do the action now (doesn't change when it runs next)."""
    s = await _schedule(db, auth, sid)
    _require(auth, s.home_id, SCENES)
    return await run_schedule(db, request.app.state.control, s, "start")


# ---------------------------------------------------------------- internal: the scheduler
class FireIn(BaseModel):
    phase: str = Field(pattern="^(start|until)$")
    at: dt.datetime


def _internal(request: Request) -> None:
    """Only the scheduler: loopback, not through nginx (which adds X-Forwarded-For), with the token."""
    token = getattr(request.app.state, "internal_token", None)
    sent = request.headers.get(INTERNAL_HEADER, "")
    client = request.client.host if request.client else ""
    hosts = getattr(request.app.state, "internal_hosts", set())
    if (not token or not hmac.compare_digest(sent, token) or client not in hosts
            or "x-forwarded-for" in request.headers):
        raise HTTPException(404)


@router.post("/internal/schedules/{sid}/fire", include_in_schema=False)
async def fire(sid: int, body: FireIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Run one due event, once: an event no newer than the last one fired is ignored, so a
    scheduler retry or restart can't water the garden twice."""
    _internal(request)
    s = (await db.execute(select(Schedule).where(Schedule.id == sid).with_for_update())).scalar_one_or_none()
    if s is None or not s.enabled:
        return {"fired": False, "reason": "gone_or_disabled"}
    at = body.at.astimezone(UTC)
    last = s.last_start if body.phase == "start" else s.last_until
    if last is not None and at <= last:
        return {"fired": False, "reason": "already_fired"}
    if body.phase == "start":
        s.last_start = at
    else:
        s.last_until = at
    await db.commit()  # at most once: marked before acting
    result = await run_schedule(db, request.app.state.control, s, body.phase)
    s.last_result = {"phase": body.phase, "at": at.isoformat(), **{k: v for k, v in result.items() if k != "run_id"}}
    db.add(AuditLog(src=f"sched:{sid}", action=f"schedule_{body.phase}", target=f"home:{s.home_id}",
                    detail={"name": s.name, "error": result.get("error")}))
    await db.commit()
    return {"fired": True, "result": result}
