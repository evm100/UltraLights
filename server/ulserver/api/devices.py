"""Device control for signed-in users (REST mirror of the WebSocket set/live messages)."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..bridge import NotConnected
from ..control import Control, ControlError
from ..db import get_db
from ..models import Home, Node, Room
from ..security import Auth, require_auth, src_for
from ..views import can_see, devices_for, visible_home_ids

router = APIRouter(prefix="/api")


async def visible_node(db: AsyncSession, auth: Auth, dev: str) -> Node:
    node = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one_or_none()
    if node is None or not can_see(node, await visible_home_ids(db, auth.user)):
        raise HTTPException(404, "no such device")
    return node


def control_error(e: Exception) -> HTTPException:
    if isinstance(e, ControlError):
        return HTTPException(404 if e.code == "no_such_output" else 422, {"error": e.code, "detail": e.detail})
    if isinstance(e, NotConnected):
        return HTTPException(503, {"error": "broker_offline", "detail": str(e)})
    raise e


@router.get("/devices")
async def list_devices(auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await devices_for(db, auth.user)


@router.get("/homes")
async def list_homes(auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    ids = await visible_home_ids(db, auth.user)
    q = select(Home).order_by(Home.name)
    if ids is not None:
        q = q.where(Home.id.in_(ids))
    homes = (await db.execute(q)).scalars().all()
    rooms = (await db.execute(select(Room).where(Room.home_id.in_([h.id for h in homes])).order_by(Room.sort, Room.name))).scalars().all()
    return [{"id": h.id, "name": h.name,
             "rooms": [{"id": r.id, "name": r.name} for r in rooms if r.home_id == h.id]} for h in homes]


@router.put("/devices/{dev}/outputs/{out}")
async def set_output(dev: str, out: str, body: dict[str, Any], request: Request,
                     auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    await visible_node(db, auth, dev)
    control: Control = request.app.state.control
    try:
        return await control.set(dev, out, body, src_for(auth))
    except (ControlError, NotConnected) as e:
        raise control_error(e) from None


@router.post("/devices/{dev}/outputs/{out}/pulse")
async def pulse(dev: str, out: str, body: dict[str, Any], request: Request,
                auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    await visible_node(db, auth, dev)
    control: Control = request.app.state.control
    try:
        return await control.cmd(dev, "pulse", {"out": out, "ms": int(body.get("ms", 1000))}, src_for(auth), wait=5)
    except (ControlError, NotConnected) as e:
        raise control_error(e) from None
