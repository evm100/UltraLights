"""A home for the people in it: rooms, the room toggle, device and output settings, members
and invites. Every route checks the caller's home abilities (access.py); admins pass all.
"""
import datetime as dt
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import MANAGE, PEOPLE, AccessDenied, load_access
from ..actions import room_power as switch_room
from ..bridge import NotConnected
from ..db import get_db
from ..manage import ManageError, output_settings, push_device, rename_device, set_member, update_output
from ..models import AuditLog, Home, HomeMember, Invite, Node, Output, Room, User
from ..security import (
    Auth,
    check_csrf,
    create_session,
    hash_password,
    optional_auth,
    require_auth,
    set_session_cookie,
    sha256,
    src_for,
)

router = APIRouter(prefix="/api")
UTC = dt.timezone.utc
INVITE_DAYS = 7


def _err(e: Exception) -> HTTPException:
    if isinstance(e, AccessDenied):
        return HTTPException(403 if e.code == "forbidden" else 404, {"error": e.code, "detail": e.detail})
    if isinstance(e, ManageError):
        return HTTPException(e.status, {"error": e.code, "detail": e.detail})
    raise e


async def _home(db: AsyncSession, auth: Auth, hid: int, ability: str) -> Home:
    try:
        auth.access.require_home(hid, ability)
    except AccessDenied as e:
        raise _err(e) from None
    h = await db.get(Home, hid)
    if h is None:
        raise HTTPException(404, {"error": "no_such_home"})
    return h


async def _room(db: AsyncSession, hid: int, rid: int) -> Room:
    r = await db.get(Room, rid)
    if r is None or r.home_id != hid:
        raise HTTPException(404, {"error": "no_such_room"})
    return r


async def _managed_node(db: AsyncSession, auth: Auth, dev: str) -> Node:
    """A node the caller may manage. Anything else is reported missing, as for control."""
    n = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one_or_none()
    if n is None or not auth.access.sees_home(n.home_id):
        raise HTTPException(404, {"error": "no_such_device"})
    try:
        auth.access.require_home(n.home_id, MANAGE)
    except AccessDenied as e:
        raise _err(e) from None
    return n


# ---------------------------------------------------------------- homes and rooms
@router.get("/homes")
async def list_homes(auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    ids = auth.access.home_ids()
    q = select(Home).order_by(Home.name)
    if ids is not None:
        q = q.where(Home.id.in_(ids))
    homes = (await db.execute(q)).scalars().all()
    rooms = (await db.execute(select(Room).where(Room.home_id.in_([h.id for h in homes]))
                              .order_by(Room.sort, Room.name))).scalars().all()
    return [{"id": h.id, "name": h.name, "role": auth.access.role(h.id), "can": sorted(auth.access.home_can(h.id)),
             "timezone": h.timezone, "has_location": h.latitude is not None,
             "rooms": [{"id": r.id, "name": r.name} for r in rooms if r.home_id == h.id]} for h in homes]


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class RoomOrder(BaseModel):
    room_ids: list[int]


@router.post("/homes/{hid}/rooms")
async def create_room(hid: int, body: RoomIn, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, MANAGE)
    last = (await db.execute(select(Room.sort).where(Room.home_id == hid).order_by(Room.sort.desc()).limit(1))).scalar()
    r = Room(home_id=hid, name=body.name.strip(), sort=(last or 0) + 1)
    db.add(r)
    db.add(AuditLog(src=src_for(auth), action="room_created", target=f"home:{hid}", detail={"name": r.name}))
    await db.commit()
    return {"id": r.id, "name": r.name}


@router.put("/homes/{hid}/rooms/order")
async def order_rooms(hid: int, body: RoomOrder, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, MANAGE)
    rooms = {r.id: r for r in (await db.execute(select(Room).where(Room.home_id == hid))).scalars()}
    if sorted(body.room_ids) != sorted(rooms):
        raise HTTPException(422, {"error": "invalid", "detail": "list every room of the home exactly once"})
    for i, rid in enumerate(body.room_ids):
        rooms[rid].sort = i
    await db.commit()
    return {"ok": True}


@router.put("/homes/{hid}/rooms/{rid}")
async def rename_room(hid: int, rid: int, body: RoomIn, auth: Auth = Depends(require_auth),
                      db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, MANAGE)
    r = await _room(db, hid, rid)
    r.name = body.name.strip()
    db.add(AuditLog(src=src_for(auth), action="room_renamed", target=f"room:{rid}", detail={"name": r.name}))
    await db.commit()
    return {"ok": True}


@router.delete("/homes/{hid}/rooms/{rid}")
async def delete_room(hid: int, rid: int, request: Request, auth: Auth = Depends(require_auth),
                      db: AsyncSession = Depends(get_db)) -> dict:
    """Its lights stay, unassigned."""
    await _home(db, auth, hid, MANAGE)
    r = await _room(db, hid, rid)
    node_ids = {n for (n,) in await db.execute(select(Output.node_id).where(Output.room_id == rid))}
    await db.execute(update(Output).where(Output.room_id == rid).values(room_id=None))
    await db.delete(r)
    db.add(AuditLog(src=src_for(auth), action="room_deleted", target=f"room:{rid}", detail={"name": r.name}))
    await db.commit()
    for n in (await db.execute(select(Node).where(Node.id.in_(node_ids)))).scalars():
        await push_device(db, request.app.state.hub, n)
    return {"ok": True}


class PowerIn(BaseModel):
    on: bool


@router.post("/homes/{hid}/rooms/{rid}/power")
async def room_power(hid: int, rid: int, body: PowerIn, request: Request, auth: Auth = Depends(require_auth),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """One-tap room on/off: every light in the room the caller can control. Relays and
    valves keep their own controls, so a room toggle never starts a pump or opens a valve."""
    if not auth.access.sees_home(hid):
        raise HTTPException(404, {"error": "no_such_home"})
    await _room(db, hid, rid)
    try:
        r = await switch_room(db, request.app.state.control, hid, rid, body.on, auth.access, src_for(auth))
    except NotConnected:
        raise HTTPException(503, {"error": "broker_offline"}) from None
    return {"on": body.on, **r}


# ---------------------------------------------------------------- devices and outputs
@router.get("/homes/{hid}/devices")
async def device_settings(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Settings view for the home's manage page."""
    await _home(db, auth, hid, MANAGE)
    nodes = (await db.execute(select(Node).where(Node.home_id == hid).order_by(Node.name))).scalars().all()
    outs = (await db.execute(select(Output).where(Output.node_id.in_([n.id for n in nodes])).order_by(Output.key))).scalars().all()
    return [{"id": n.device_id, "name": n.name, "kind": n.kind, "online": n.online,
             "outputs": [output_settings(n, o) for o in outs if o.node_id == n.id]} for n in nodes]


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.put("/devices/{dev}")
async def put_device(dev: str, body: DeviceIn, request: Request, auth: Auth = Depends(require_auth),
                     db: AsyncSession = Depends(get_db)) -> dict:
    n = await _managed_node(db, auth, dev)
    await rename_device(db, request.app.state.hub, n, body.name, src_for(auth))
    return {"ok": True}


class OutputSettingsIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    room_id: int | None = None
    max_level: float | None = Field(default=None, ge=0.05, le=1)
    power_on: str | None = Field(default=None, pattern="^(last|off|on)$")
    max_on_ms: int | None = None  # valves: 1 min to 2 h, enforced on the device


@router.put("/devices/{dev}/outputs/{out}/settings")
async def put_output_settings(dev: str, out: str, body: OutputSettingsIn, request: Request,
                              auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    """Only the fields sent change; room_id null takes the output out of its room."""
    n = await _managed_node(db, auth, dev)
    o = (await db.execute(select(Output).where(Output.node_id == n.id, Output.key == out))).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, {"error": "no_such_output"})
    changes = body.model_dump(include=body.model_fields_set)
    try:
        return await update_output(db, request.app.state.control, request.app.state.hub, n, o, changes, src_for(auth))
    except ManageError as e:
        raise _err(e) from None


# ---------------------------------------------------------------- members
class RoleIn(BaseModel):
    role: str = Field(pattern="^(owner|member)$")  # guests arrive with grants


@router.get("/homes/{hid}/members")
async def members(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _home(db, auth, hid, PEOPLE)
    rows = (await db.execute(select(HomeMember, User).join(User, User.id == HomeMember.user_id)
                             .where(HomeMember.home_id == hid).order_by(User.username))).all()
    return [{"user_id": u.id, "username": u.username, "display_name": u.display_name or u.username,
             "role": m.role, "you": u.id == auth.user.id} for m, u in rows]


@router.put("/homes/{hid}/members/{uid}")
async def put_member(hid: int, uid: int, body: RoleIn, request: Request, auth: Auth = Depends(require_auth),
                     db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, PEOPLE)
    try:
        await set_member(db, hid, uid, body.role, src_for(auth))
    except ManageError as e:
        raise _err(e) from None
    request.app.state.hub.refresh_user(uid)
    return {"ok": True}


@router.delete("/homes/{hid}/members/{uid}")
async def delete_member(hid: int, uid: int, request: Request, auth: Auth = Depends(require_auth),
                        db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, PEOPLE)
    try:
        await set_member(db, hid, uid, None, src_for(auth))
    except ManageError as e:
        raise _err(e) from None
    request.app.state.hub.refresh_user(uid)
    return {"ok": True}


# ---------------------------------------------------------------- invites
def _invite_view(i: Invite) -> dict[str, Any]:
    return {"id": i.id, "role": i.role, "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat()}


@router.post("/homes/{hid}/invites")
async def create_invite(hid: int, body: RoleIn, request: Request, auth: Auth = Depends(require_auth),
                        db: AsyncSession = Depends(get_db)) -> dict:
    """A single-use link, shown once. Only its hash is stored."""
    await _home(db, auth, hid, PEOPLE)
    token = secrets.token_urlsafe(32)
    i = Invite(token_hash=sha256(token), home_id=hid, role=body.role, created_by=auth.user.id,
               expires_at=dt.datetime.now(UTC) + dt.timedelta(days=INVITE_DAYS))
    db.add(i)
    db.add(AuditLog(src=src_for(auth), action="invite_created", target=f"home:{hid}", detail={"role": body.role}))
    await db.commit()
    await db.refresh(i)
    host = request.app.state.settings.public_host
    return {**_invite_view(i), "token": token, "url": f"https://{host}/#/invite/{token}"}


@router.get("/homes/{hid}/invites")
async def list_invites(hid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _home(db, auth, hid, PEOPLE)
    now = dt.datetime.now(UTC)
    rows = (await db.execute(select(Invite).where(Invite.home_id == hid, Invite.used_at.is_(None),
                                                  Invite.revoked.is_(False), Invite.expires_at > now)
                             .order_by(Invite.created_at))).scalars().all()
    return [_invite_view(i) for i in rows]


@router.delete("/homes/{hid}/invites/{iid}")
async def revoke_invite(hid: int, iid: int, auth: Auth = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    await _home(db, auth, hid, PEOPLE)
    i = await db.get(Invite, iid)
    if i is None or i.home_id != hid:
        raise HTTPException(404, {"error": "no_such_invite"})
    i.revoked = True
    db.add(AuditLog(src=src_for(auth), action="invite_revoked", target=f"home:{hid}", detail={"invite": iid}))
    await db.commit()
    return {"ok": True}


async def _open_invite(db: AsyncSession, token: str) -> Invite:
    i = (await db.execute(select(Invite).where(Invite.token_hash == sha256(token)))).scalar_one_or_none()
    if i is None or i.revoked or i.used_at is not None or i.expires_at < dt.datetime.now(UTC):
        raise HTTPException(404, {"error": "invite_invalid", "detail": "this invite link is used, revoked or expired"})
    return i


@router.get("/invites/{token}")
async def view_invite(token: str, auth: Auth | None = Depends(optional_auth), db: AsyncSession = Depends(get_db)) -> dict:
    """What the link offers, for the page shown before accepting. Works signed out."""
    i = await _open_invite(db, token)
    home = await db.get(Home, i.home_id)
    by = await db.get(User, i.created_by) if i.created_by else None
    return {"home": home.name if home else "", "role": i.role,
            "invited_by": (by.display_name or by.username) if by else None,
            "expires_at": i.expires_at.isoformat(),
            "signed_in_as": auth.user.username if auth else None}


class AcceptIn(BaseModel):
    """Signed out: a new account. Signed in: empty, the current account joins."""
    username: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    password: str | None = Field(default=None, min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=64)


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, body: AcceptIn, request: Request, response: Response,
                        auth: Auth | None = Depends(optional_auth), db: AsyncSession = Depends(get_db)) -> dict:
    i = await _open_invite(db, token)
    if auth is not None:
        check_csrf(request, auth)
        user = auth.user
    else:
        if not body.username or not body.password:
            raise HTTPException(422, {"error": "invalid", "detail": "choose a username and a password (10+ characters)"})
        if (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none():
            raise HTTPException(409, {"error": "username_taken"})
        user = User(username=body.username, password_hash=hash_password(body.password),
                    display_name=body.display_name.strip())
        db.add(user)
        await db.flush()
        db.add(AuditLog(src=f"invite:{i.id}", action="user_created", target=user.username, detail={"home_id": i.home_id}))
    m = await db.get(HomeMember, (i.home_id, user.id))
    if m is None:
        db.add(HomeMember(home_id=i.home_id, user_id=user.id, role=i.role))
    elif m.role != "owner":
        m.role = i.role  # an invite never demotes an owner
    i.used_at, i.used_by = dt.datetime.now(UTC), user.id
    db.add(AuditLog(src=f"ui:{user.id}", action="invite_accepted", target=f"home:{i.home_id}", detail={"role": i.role}))
    if auth is None:
        tok, _ = await create_session(db, user)
        set_session_cookie(response, tok)
    await db.commit()
    request.app.state.hub.refresh_user(user.id)
    access = await load_access(db, user)
    return {"home_id": i.home_id, "role": access.role(i.home_id)}
