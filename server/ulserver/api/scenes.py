"""Scenes for the people in a home: list, create (from a document or the current look),
edit, delete, apply and undo. Creating and editing need the scenes ability; applying needs
control of each item (items the person can't control are skipped and reported)."""
import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import SCENES, AccessDenied
from ..control import Control
from ..db import get_db
from ..models import AuditLog, Scene, SceneRun
from ..scenes import SceneError, apply, capture, check, undo
from ..security import Auth, require_auth, src_for

router = APIRouter(prefix="/api")


def _view(s: Scene) -> dict[str, Any]:
    doc = s.doc
    return {"id": s.id, "home_id": s.home_id, "room_id": s.room_id, "name": s.name,
            "fade_ms": doc.get("fade_ms"), "items": doc["items"], "updated_at": s.updated_at.isoformat()}


def _invalid(e: SceneError) -> HTTPException:
    return HTTPException(422, {"error": "scene_invalid", "errors": e.errors})


def _sees(auth: Auth, hid: int) -> None:
    if not auth.access.sees_home(hid):
        raise HTTPException(404, {"error": "no_such_home"})


def _may_edit(auth: Auth, hid: int) -> None:
    try:
        auth.access.require_home(hid, SCENES)
    except AccessDenied as e:
        raise HTTPException(403 if e.code == "forbidden" else 404, {"error": e.code, "detail": e.detail}) from None


async def _scene(db: AsyncSession, auth: Auth, sid: int) -> Scene:
    s = await db.get(Scene, sid)
    if s is None or not auth.access.sees_home(s.home_id):
        raise HTTPException(404, {"error": "no_such_scene"})
    return s


def _save(s: Scene, doc: dict[str, Any]) -> None:
    s.doc, s.name, s.room_id = doc, doc["name"], doc.get("room_id")
    s.updated_at = dt.datetime.now(dt.timezone.utc)


@router.get("/homes/{hid}/scenes")
async def list_scenes(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    _sees(auth, hid)
    rows = (await db.execute(select(Scene).where(Scene.home_id == hid).order_by(Scene.name))).scalars().all()
    return [_view(s) for s in rows]


@router.post("/homes/{hid}/scenes")
async def create_scene(hid: int, doc: dict[str, Any], request: Request, auth: Auth = Depends(require_auth),
                       db: AsyncSession = Depends(get_db)) -> dict:
    _may_edit(auth, hid)
    try:
        await check(db, request.app.state.control, hid, doc)
    except SceneError as e:
        raise _invalid(e) from None
    s = Scene(home_id=hid, created_by=auth.user.id, name="", doc={})
    _save(s, doc)
    db.add(s)
    db.add(AuditLog(src=src_for(auth), action="scene_created", target=f"home:{hid}", detail={"name": s.name}))
    await db.commit()
    return _view(s)


class CaptureIn(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    room_id: int | None = None
    fade_ms: int | None = Field(default=None, ge=0, le=60000)


@router.post("/homes/{hid}/scenes/capture")
async def capture_scene(hid: int, body: CaptureIn, request: Request, auth: Auth = Depends(require_auth),
                        db: AsyncSession = Depends(get_db)) -> dict:
    """Save the current look of a room's lights (or the whole home's) as a new scene."""
    _may_edit(auth, hid)
    items = await capture(db, hid, body.room_id, auth.access)
    if not items:
        raise HTTPException(422, {"error": "scene_invalid", "errors": ["no lights there have a state to capture yet"]})
    doc: dict[str, Any] = {"v": 1, "name": body.name.strip(), "room_id": body.room_id, "items": items}
    if body.fade_ms is not None:
        doc["fade_ms"] = body.fade_ms
    return await create_scene(hid, doc, request, auth, db)


@router.put("/scenes/{sid}")
async def put_scene(sid: int, doc: dict[str, Any], request: Request, auth: Auth = Depends(require_auth),
                    db: AsyncSession = Depends(get_db)) -> dict:
    s = await _scene(db, auth, sid)
    _may_edit(auth, s.home_id)
    try:
        await check(db, request.app.state.control, s.home_id, doc)
    except SceneError as e:
        raise _invalid(e) from None
    _save(s, doc)
    db.add(AuditLog(src=src_for(auth), action="scene_changed", target=f"scene:{sid}", detail={"name": s.name}))
    await db.commit()
    return _view(s)


@router.post("/scenes/{sid}/recapture")
async def recapture_scene(sid: int, request: Request, auth: Auth = Depends(require_auth),
                          db: AsyncSession = Depends(get_db)) -> dict:
    """Keep the scene's outputs, take their current look."""
    s = await _scene(db, auth, sid)
    _may_edit(auth, s.home_id)
    now = {(i["dev"], i["out"]): i for i in await capture(db, s.home_id, None, auth.access)}
    doc = dict(s.doc)
    doc["items"] = [now.get((i["dev"], i["out"]), i) for i in s.doc["items"]]
    doc["room_id"] = s.room_id
    return await put_scene(sid, doc, request, auth, db)


@router.delete("/scenes/{sid}")
async def delete_scene(sid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    s = await _scene(db, auth, sid)
    _may_edit(auth, s.home_id)
    await db.delete(s)
    db.add(AuditLog(src=src_for(auth), action="scene_deleted", target=f"scene:{sid}", detail={"name": s.name}))
    await db.commit()
    return {"ok": True}


@router.post("/scenes/{sid}/apply")
async def apply_scene(sid: int, request: Request, auth: Auth = Depends(require_auth),
                      db: AsyncSession = Depends(get_db)) -> dict:
    s = await _scene(db, auth, sid)
    control: Control = request.app.state.control
    r = await apply(db, control, s, auth.access, src_for(auth))
    if r["broker_offline"] and not r["set"]:
        raise HTTPException(503, {"error": "broker_offline"})
    return r


@router.post("/runs/{rid}/undo")
async def undo_run(rid: int, request: Request, auth: Auth = Depends(require_auth),
                   db: AsyncSession = Depends(get_db)) -> dict:
    run = await db.get(SceneRun, rid)
    if run is None or not auth.access.sees_home(run.home_id):
        raise HTTPException(404, {"error": "no_such_run"})
    try:
        return await undo(db, request.app.state.control, run, auth.access, src_for(auth))
    except SceneError as e:
        raise HTTPException(409, {"error": "already_undone", "detail": e.errors[0]}) from None
