"""Device control for signed-in users (REST mirror of the WebSocket set/live messages).

Every route checks the caller's access: an output they can't see is reported as missing
(404) so its existence doesn't leak; one they can see but lack the ability for is 403.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import CONTROL, AccessDenied, abilities_for
from ..bridge import NotConnected
from ..control import Control, ControlError, OutputInfo
from ..db import get_db
from ..security import Auth, require_auth, src_for
from ..views import devices_for

router = APIRouter(prefix="/api")


def control_error(e: Exception) -> HTTPException:
    if isinstance(e, AccessDenied):
        return HTTPException(403 if e.code == "forbidden" else 404, {"error": e.code, "detail": e.detail})
    if isinstance(e, ControlError):
        return HTTPException(404 if e.code == "no_such_output" else 422, {"error": e.code, "detail": e.detail})
    if isinstance(e, NotConnected):
        return HTTPException(503, {"error": "broker_offline", "detail": str(e)})
    raise e


def authorized(control: Control, auth: Auth, dev: str, out: str, needed: set[str]) -> OutputInfo:
    info = control.get(dev, out)
    auth.access.require(info, needed)
    return info


@router.get("/devices")
async def list_devices(auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await devices_for(db, auth.access)


@router.put("/devices/{dev}/outputs/{out}")
async def set_output(dev: str, out: str, body: dict[str, Any], request: Request,
                     auth: Auth = Depends(require_auth)) -> dict:
    control: Control = request.app.state.control
    try:
        authorized(control, auth, dev, out, abilities_for(body))
        return await control.set(dev, out, body, src_for(auth))
    except (AccessDenied, ControlError, NotConnected) as e:
        raise control_error(e) from None


@router.post("/devices/{dev}/outputs/{out}/pulse")
async def pulse(dev: str, out: str, body: dict[str, Any], request: Request,
                auth: Auth = Depends(require_auth)) -> dict:
    control: Control = request.app.state.control
    try:
        authorized(control, auth, dev, out, {CONTROL})
        return await control.cmd(dev, "pulse", {"out": out, "ms": int(body.get("ms", 1000))}, src_for(auth), wait=5)
    except (AccessDenied, ControlError, NotConnected) as e:
        raise control_error(e) from None
