"""Admin panel API: nodes and the builder, provisioning, config, commands, tokens,
certificates, firmware packages, users, homes and the audit log."""
import base64
import datetime as dt
import secrets
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import contracts
from .. import firmware as fwm
from ..access import ADMIN_ACCESS, HOME_ROLES
from ..bridge import NotConnected
from ..control import Control, ControlError
from ..db import get_db
from ..enroll import issue_token, write_crl
from ..manage import ManageError, update_output
from ..models import (
    AuditLog,
    BoardTemplate,
    Certificate,
    EnrollToken,
    FirmwarePackage,
    Home,
    HomeMember,
    Node,
    Output,
    Room,
    User,
)
from ..security import Auth, hash_password, require_admin, src_for
from ..views import device_view
from .devices import control_error

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
UTC = dt.timezone.utc


def _src(auth: Auth) -> str:
    return src_for(auth, "admin")


async def _node(db: AsyncSession, dev: str) -> Node:
    n = (await db.execute(select(Node).where(Node.device_id == dev))).scalar_one_or_none()
    if n is None:
        raise HTTPException(404, "no such node")
    return n


def _ctl(request: Request) -> Control:
    return request.app.state.control


def _require_board_node(n: Node) -> None:
    if n.kind != "node":
        raise HTTPException(422, {"error": "not_a_board", "detail": f"{n.device_id} is a {n.bridge} bridge; it has no board"})


# ---------------------------------------------------------------- boards
class BoardIn(BaseModel):
    board: dict[str, Any]


@router.post("/boards/check")
async def check_board(body: BoardIn) -> dict:
    return contracts.check_board(body.board)


@router.get("/templates")
async def templates(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(BoardTemplate).order_by(BoardTemplate.name))).scalars().all()
    return [{"id": t.id, "name": t.name, "board": t.board} for t in rows]


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    board: dict[str, Any]


@router.post("/templates")
async def save_template(body: TemplateIn, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    res = contracts.check_board(body.board)
    if res["errors"]:
        raise HTTPException(422, {"error": "board_invalid", "checks": res})
    t = (await db.execute(select(BoardTemplate).where(BoardTemplate.name == body.name))).scalar_one_or_none()
    if t is None:
        t = BoardTemplate(name=body.name, board=body.board)
        db.add(t)
    else:
        t.board = body.board
    db.add(AuditLog(src=_src(auth), action="template_saved", target=body.name))
    await db.commit()
    return {"id": t.id, "name": t.name, "board": t.board}


@router.delete("/templates/{tid}")
async def delete_template(tid: int, db: AsyncSession = Depends(get_db)) -> dict:
    await db.execute(delete(BoardTemplate).where(BoardTemplate.id == tid))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- nodes
class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    board: dict[str, Any]
    home_id: int | None = None


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    board: dict[str, Any] | None = None
    home_id: int | None = None
    clear_home: bool = False


def _require_clean_board(board: dict) -> dict:
    res = contracts.check_board(board)
    if res["errors"]:
        raise HTTPException(422, {"error": "board_invalid", "checks": res})
    return res


async def _new_device_id(db: AsyncSession) -> str:
    for _ in range(20):
        d = f"ul-{secrets.token_hex(3)}"
        if (await db.execute(select(Node.id).where(Node.device_id == d))).first() is None:
            return d
    raise HTTPException(500, "could not allocate a device id")


@router.get("/nodes")
async def list_nodes(request: Request, db: AsyncSession = Depends(get_db)) -> list[dict]:
    nodes = (await db.execute(select(Node).order_by(Node.name))).scalars().all()
    outs = (await db.execute(select(Output))).scalars().all()
    by: dict[int, list[Output]] = {}
    for o in outs:
        by.setdefault(o.node_id, []).append(o)
    errs = request.app.state.bridge.contract_errors
    res = []
    for n in nodes:
        v = device_view(n, by.get(n.id, []), ADMIN_ACCESS)
        v["contract_errors"] = len(errs.get(n.device_id, []))
        res.append(v)
    return res


@router.post("/nodes")
async def create_node(body: NodeIn, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    checks = _require_clean_board(body.board)
    if body.home_id is not None and await db.get(Home, body.home_id) is None:
        raise HTTPException(422, "no such home")
    n = Node(device_id=await _new_device_id(db), name=body.name, chip=body.board["chip"], board=body.board,
             home_id=body.home_id, config_rev=0)
    db.add(n)
    db.add(AuditLog(src=_src(auth), action="node_created", target=n.device_id, detail={"board": body.board.get("board")}))
    await db.commit()
    return {"id": n.device_id, "checks": checks}


@router.get("/nodes/{dev}")
async def node_detail(dev: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    n = await _node(db, dev)
    outs = (await db.execute(select(Output).where(Output.node_id == n.id))).scalars().all()
    toks = (await db.execute(select(EnrollToken).where(EnrollToken.node_id == n.id).order_by(EnrollToken.id.desc()))).scalars().all()
    certs = (await db.execute(select(Certificate).where(Certificate.node_id == n.id).order_by(Certificate.id.desc()))).scalars().all()
    v = device_view(n, list(outs), ADMIN_ACCESS)
    is_node = n.kind == "node"
    v.update({
        "board": n.board,
        "config": n.config,
        "default_outputs": contracts.default_outputs(n.board) if is_node else [],
        "info": n.info,
        "checks": contracts.check_board(n.board) if is_node else {"errors": [], "warnings": []},
        "tokens": [_token_view(t, n.device_id) for t in toks],
        "certs": [_cert_view(c) for c in certs],
        "contract_errors": list(request.app.state.bridge.contract_errors.get(dev, [])),
    })
    return v


@router.put("/nodes/{dev}")
async def update_node(dev: str, body: NodePatch, request: Request, auth: Auth = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    n = await _node(db, dev)
    if body.name is not None:
        n.name = body.name
    if body.clear_home:
        n.home_id = None
    elif body.home_id is not None:
        if await db.get(Home, body.home_id) is None:
            raise HTTPException(422, "no such home")
        n.home_id = body.home_id
    if body.board is not None:
        _require_board_node(n)
        _require_clean_board(body.board)
        if body.board["chip"] != n.chip and n.enrolled_at is not None:
            raise HTTPException(422, "chip can't change on an enrolled node; create a new node instead")
        n.chip = body.board["chip"]
        n.board = body.board
        if n.config is not None and contracts.check_config(n.board, n.config):
            n.config = None  # mapping no longer fits the board; back to the default
        db.add(AuditLog(src=_src(auth), action="board_changed", target=dev))
    await db.commit()
    await _ctl(request).load_node(db, n)
    return {"ok": True}


@router.delete("/nodes/{dev}")
async def delete_node(dev: str, request: Request, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    n = await _node(db, dev)
    keys = [o.key for o in (await db.execute(select(Output).where(Output.node_id == n.id))).scalars()]
    now = dt.datetime.now(UTC)
    certs = (await db.execute(select(Certificate).where(Certificate.node_id == n.id, Certificate.revoked_at.is_(None)))).scalars().all()
    for c in certs:
        c.revoked_at, c.revoke_reason = now, "node_deleted"
    await db.flush()
    await db.delete(n)
    db.add(AuditLog(src=_src(auth), action="node_deleted", target=dev))
    await db.commit()
    if certs:
        await write_crl(db, request.app.state.pki)
    try:
        await _ctl(request).clear_device(dev, keys)
    except NotConnected:
        pass  # retained messages stay until the next delete; the broker ACL still binds them to the old id
    return {"ok": True}


# ---------------------------------------------------------------- bridges
class BridgeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    bridge: str = Field(pattern="^openrgb$")
    home_id: int | None = None


@router.post("/bridges")
async def create_bridge(body: BridgeIn, request: Request, auth: Auth = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)) -> dict:
    """A bridged device (the OpenRGB agent on a PC). It has no board: the agent enrolls with the
    returned one-time token and then describes its parts in info."""
    if body.home_id is not None and await db.get(Home, body.home_id) is None:
        raise HTTPException(422, "no such home")
    n = Node(device_id=await _new_device_id(db), name=body.name, kind="bridge", bridge=body.bridge, chip=None,
             board=None, home_id=body.home_id, config_rev=0)
    db.add(n)
    await db.flush()
    db.add(AuditLog(src=_src(auth), action="bridge_created", target=n.device_id, detail={"bridge": body.bridge}))
    pairing = await _pairing(request, db, n, auth)
    return {"id": n.device_id, **pairing}


async def _pairing(request: Request, db: AsyncSession, n: Node, auth: Auth) -> dict:
    s = request.app.state.settings
    tok = await issue_token(db, n, auth.user.id, s.token_days)
    await db.commit()
    return {"token": tok, "server": s.public_host,
            "token_expires": (dt.datetime.now(UTC) + dt.timedelta(days=s.token_days)).isoformat()}


@router.post("/nodes/{dev}/pairing")
async def bridge_pairing(dev: str, request: Request, auth: Auth = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)) -> dict:
    """New one-time enrollment token for a bridge agent (re-installs, moved host, lost key)."""
    n = await _node(db, dev)
    if n.kind != "bridge":
        raise HTTPException(422, {"error": "not_a_bridge", "detail": "nodes get their token in the ul_board blob"})
    return {"id": n.device_id, **await _pairing(request, db, n, auth)}


# ---------------------------------------------------------------- provisioning
class ProvisionIn(BaseModel):
    token: bool = True
    server: str | None = None
    wifi_ssid: str | None = None
    wifi_psk: str | None = None
    dns: str | None = None
    server_ca: bool | None = None  # None = include when the server is configured with one (dev)
    size: int = 4096


@router.post("/nodes/{dev}/provision")
async def provision(dev: str, body: ProvisionIn, request: Request, auth: Auth = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """Build the ul_board blob (board + provisioning) for flashing. Issues a fresh one-time token
    unless token=false (descriptor-only rewrite of an enrolled node)."""
    s = request.app.state.settings
    n = await _node(db, dev)
    _require_board_node(n)
    _require_clean_board(n.board)
    prov: dict[str, Any] = {"device_id": n.device_id, "server": body.server or s.public_host}
    if s.mqtt_port != 8883:
        prov["mqtt_port"] = s.mqtt_port
    tok = None
    if body.token:
        tok = await issue_token(db, n, auth.user.id, s.token_days)
        prov["token"] = tok
    want_ca = body.server_ca if body.server_ca is not None else bool(s.provision_server_ca_file)
    if want_ca:
        if not s.provision_server_ca_file:
            raise HTTPException(422, "no server CA configured (UL_PROVISION_SERVER_CA_FILE)")
        with open(s.provision_server_ca_file) as f:
            pem = f.read()
        prov["server_ca"] = pem[: pem.index("-----END CERTIFICATE-----") + 25] + "\n"
    if body.wifi_ssid:
        prov["wifi"] = {"ssid": body.wifi_ssid}
        if body.wifi_psk:
            prov["wifi"]["psk"] = body.wifi_psk
    if body.dns:
        prov["dns"] = body.dns
    payload = {"v": 1, "board": n.board, "provision": prov}
    try:
        contracts.validate("partition", payload)
        ub = contracts.ulboard()
        blob = ub.encode(payload, body.size)
    except (contracts.ContractError, ValueError) as e:
        raise HTTPException(422, {"error": "provision_invalid", "detail": str(e)}) from None
    crc = ub.crc_hex(payload)
    n.board_crc = crc
    db.add(AuditLog(src=_src(auth), action="provisioned", target=dev,
                    detail={"crc": crc, "token": bool(tok), "wifi": bool(body.wifi_ssid), "server": prov["server"]}))
    await db.commit()
    return {
        "device_id": n.device_id,
        "chip": n.chip,
        "crc": crc,
        "size": len(blob),
        "blob_b64": base64.b64encode(blob).decode(),
        "token_issued": tok is not None,
        "token_expires": (dt.datetime.now(UTC) + dt.timedelta(days=s.token_days)).isoformat() if tok else None,
        "server": prov["server"],
        "server_ca": "server_ca" in prov,
    }


# ---------------------------------------------------------------- config and outputs
class ConfigIn(BaseModel):
    outputs: list[dict[str, Any]] | None  # None = back to the default mapping


@router.put("/nodes/{dev}/config")
async def put_config(dev: str, body: ConfigIn, request: Request, auth: Auth = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    n = await _node(db, dev)
    _require_board_node(n)
    rev = n.config_rev + 1
    if body.outputs is None:
        cfg = None
    else:
        cfg = {"v": 1, "rev": rev, "outputs": body.outputs}
        errs = contracts.check_config(n.board, cfg)
        if errs:
            raise HTTPException(422, {"error": "config_invalid", "errors": errs})
    n.config, n.config_rev = cfg, rev
    db.add(AuditLog(src=_src(auth), action="config_changed", target=dev, detail={"rev": rev}))
    await db.commit()
    try:
        await _ctl(request).push_config(n)
    except NotConnected:
        return {"rev": rev, "published": False}
    return {"rev": rev, "published": True}


class OutputPatch(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    room_id: int | None = None
    clear_room: bool = False


@router.put("/nodes/{dev}/outputs/{key}")
async def patch_output(dev: str, key: str, body: OutputPatch, request: Request, auth: Auth = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    """Same path as an owner's edit (manage.update_output): a node's names go into its config."""
    n = await _node(db, dev)
    o = (await db.execute(select(Output).where(Output.node_id == n.id, Output.key == key))).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, "no such output")
    changes: dict[str, Any] = {}
    if body.name is not None:
        changes["name"] = body.name
    if body.clear_room:
        changes["room_id"] = None
    elif body.room_id is not None:
        changes["room_id"] = body.room_id
    try:
        return await update_output(db, _ctl(request), request.app.state.hub, n, o, changes, _src(auth))
    except ManageError as e:
        raise HTTPException(e.status, e.detail or e.code) from None


# ---------------------------------------------------------------- commands
class CmdIn(BaseModel):
    op: str
    args: dict[str, Any] = {}


@router.post("/nodes/{dev}/cmd")
async def node_cmd(dev: str, body: CmdIn, request: Request, auth: Auth = Depends(require_admin),
                   db: AsyncSession = Depends(get_db)) -> dict:
    await _node(db, dev)
    db.add(AuditLog(src=_src(auth), action=f"cmd_{body.op}"[:48], target=dev, detail=body.args or None))
    await db.commit()
    try:
        return await _ctl(request).cmd(dev, body.op, body.args, _src(auth), wait=5)
    except (ControlError, NotConnected) as e:
        raise control_error(e) from None


# ---------------------------------------------------------------- tokens and certificates
def _token_view(t: EnrollToken, dev: str) -> dict:
    now = dt.datetime.now(UTC)
    status = ("used" if t.used_at else "revoked" if t.revoked else "expired" if t.expires_at < now else "active")
    return {"id": t.id, "device_id": dev, "status": status, "created_at": t.created_at.isoformat(),
            "expires_at": t.expires_at.isoformat(), "used_at": t.used_at.isoformat() if t.used_at else None}


def _cert_view(c: Certificate) -> dict:
    return {"id": c.id, "serial": c.serial, "cn": c.cn, "issued_at": c.issued_at.isoformat(),
            "not_after": c.not_after.isoformat(), "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "revoke_reason": c.revoke_reason}


@router.get("/tokens")
async def list_tokens(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(EnrollToken, Node.device_id).join(Node, Node.id == EnrollToken.node_id)
                             .order_by(EnrollToken.id.desc()).limit(200))).all()
    return [_token_view(t, d) for t, d in rows]


@router.delete("/tokens/{tid}")
async def revoke_token(tid: int, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    t = await db.get(EnrollToken, tid)
    if t is None:
        raise HTTPException(404, "no such token")
    t.revoked = True
    db.add(AuditLog(src=_src(auth), action="token_revoked", target=str(tid)))
    await db.commit()
    return {"ok": True}


@router.get("/certs")
async def list_certs(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Certificate).order_by(Certificate.id.desc()).limit(500))).scalars().all()
    return [_cert_view(c) for c in rows]


@router.post("/certs/{cid}/revoke")
async def revoke_cert(cid: int, request: Request, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    c = await db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404, "no such certificate")
    if c.revoked_at is None:
        c.revoked_at, c.revoke_reason = dt.datetime.now(UTC), "admin"
        db.add(AuditLog(src=_src(auth), action="cert_revoked", target=c.cn, detail={"serial": c.serial}))
        await db.commit()
        await write_crl(db, request.app.state.pki)
    return _cert_view(c)


# ---------------------------------------------------------------- firmware
def _fw_view(p: FirmwarePackage) -> dict:
    return {"id": p.id, "profile": p.profile, "chip": p.chip, "version": p.version, "manifest": p.manifest,
            "uploaded_at": p.uploaded_at.isoformat()}


@router.get("/firmware")
async def list_firmware(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(FirmwarePackage).order_by(FirmwarePackage.uploaded_at.desc()))).scalars().all()
    return [_fw_view(p) for p in rows]


@router.post("/firmware")
async def upload_firmware(request: Request, file: UploadFile = File(...), auth: Auth = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)) -> dict:
    data = await file.read(fwm.MAX_ZIP + 1)
    try:
        manifest, files = fwm.read_package(data)
    except fwm.PackageError as e:
        raise HTTPException(422, {"error": "package_invalid", "detail": str(e)}) from None
    fwm.store(request.app.state.settings.firmware_dir, manifest, files)
    p = (await db.execute(select(FirmwarePackage).where(FirmwarePackage.profile == manifest["profile"],
                                                        FirmwarePackage.version == manifest["version"]))).scalar_one_or_none()
    if p is None:
        p = FirmwarePackage(profile=manifest["profile"], chip=manifest["chip"], version=manifest["version"],
                            manifest=manifest, uploaded_by=auth.user.id)
        db.add(p)
    else:
        p.manifest, p.uploaded_by, p.uploaded_at = manifest, auth.user.id, dt.datetime.now(UTC)
    db.add(AuditLog(src=_src(auth), action="firmware_uploaded", target=f"{manifest['profile']}@{manifest['version']}"))
    await db.commit()
    return _fw_view(p)


@router.get("/firmware/{pid}/files/{name}")
async def firmware_file(pid: int, name: str, request: Request, db: AsyncSession = Depends(get_db)) -> FileResponse:
    p = await db.get(FirmwarePackage, pid)
    if p is None:
        raise HTTPException(404, "no such package")
    path = fwm.file_path(request.app.state.settings.firmware_dir, p.manifest, name)
    if path is None:
        raise HTTPException(404, "no such file")
    return FileResponse(path, media_type="application/octet-stream")


@router.delete("/firmware/{pid}")
async def delete_firmware(pid: int, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    p = await db.get(FirmwarePackage, pid)
    if p is None:
        raise HTTPException(404, "no such package")
    await db.delete(p)
    db.add(AuditLog(src=_src(auth), action="firmware_deleted", target=f"{p.profile}@{p.version}"))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- users and homes
class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=256)
    is_admin: bool = False
    display_name: str = ""
    home_id: int | None = None  # optional first membership
    home_role: str = "owner"


def _check_role(role: str) -> None:
    if role not in HOME_ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(HOME_ROLES)}")


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)) -> list[dict]:
    users = (await db.execute(select(User).order_by(User.username))).scalars().all()
    mem = (await db.execute(select(HomeMember))).scalars().all()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": u.is_admin,
             "active": u.active,
             "homes": [{"home_id": m.home_id, "role": m.role} for m in mem if m.user_id == u.id]} for u in users]


@router.post("/users")
async def create_user(body: UserIn, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    if body.home_id is not None:
        _check_role(body.home_role)
        if await db.get(Home, body.home_id) is None:
            raise HTTPException(404, "no such home")
    if (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none():
        raise HTTPException(409, "username taken")
    u = User(username=body.username, password_hash=hash_password(body.password), is_admin=body.is_admin,
             display_name=body.display_name)
    db.add(u)
    await db.flush()
    if body.home_id is not None:
        db.add(HomeMember(home_id=body.home_id, user_id=u.id, role=body.home_role))
    db.add(AuditLog(src=_src(auth), action="user_created", target=body.username,
                    detail={"is_admin": body.is_admin, "home_id": body.home_id,
                            "home_role": body.home_role if body.home_id is not None else None}))
    await db.commit()
    return {"id": u.id}


class HomeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.get("/homes")
async def list_homes_admin(db: AsyncSession = Depends(get_db)) -> list[dict]:
    homes = (await db.execute(select(Home).order_by(Home.name))).scalars().all()
    rooms = (await db.execute(select(Room).order_by(Room.sort, Room.name))).scalars().all()
    counts = dict((await db.execute(select(Node.home_id, func.count()).group_by(Node.home_id))).all())
    return [{"id": h.id, "name": h.name, "nodes": counts.get(h.id, 0),
             "rooms": [{"id": r.id, "name": r.name} for r in rooms if r.home_id == h.id]} for h in homes]


@router.post("/homes")
async def create_home(body: HomeIn, auth: Auth = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    h = Home(name=body.name)
    db.add(h)
    await db.flush()
    # No automatic membership: admins see every home anyway. Add owners under People.
    db.add(AuditLog(src=_src(auth), action="home_created", target=f"home:{h.id}", detail={"name": h.name}))
    await db.commit()
    return {"id": h.id, "name": h.name}


class MemberIn(BaseModel):
    user_id: int
    role: str = "owner"


@router.post("/homes/{hid}/members")
async def add_member(hid: int, body: MemberIn, request: Request, auth: Auth = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """Add someone to a home or change their role there. Each home membership has its own
    role, so an owner of one home can be a member, guest or another owner elsewhere."""
    _check_role(body.role)
    if await db.get(Home, hid) is None or await db.get(User, body.user_id) is None:
        raise HTTPException(404, "no such home or user")
    m = await db.get(HomeMember, (hid, body.user_id))
    if m is None:
        db.add(HomeMember(home_id=hid, user_id=body.user_id, role=body.role))
    else:
        m.role = body.role
    db.add(AuditLog(src=_src(auth), action="member_set", target=f"home:{hid}",
                    detail={"user_id": body.user_id, "role": body.role}))
    await db.commit()
    request.app.state.hub.refresh_user(body.user_id)
    return {"ok": True}


@router.delete("/homes/{hid}/members/{uid}")
async def remove_member(hid: int, uid: int, request: Request, auth: Auth = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)) -> dict:
    m = await db.get(HomeMember, (hid, uid))
    if m is None:
        raise HTTPException(404, "not a member")
    await db.delete(m)
    db.add(AuditLog(src=_src(auth), action="member_removed", target=f"home:{hid}", detail={"user_id": uid}))
    await db.commit()
    request.app.state.hub.refresh_user(uid)
    return {"ok": True}


@router.post("/homes/{hid}/rooms")
async def create_room(hid: int, body: HomeIn, db: AsyncSession = Depends(get_db)) -> dict:
    if await db.get(Home, hid) is None:
        raise HTTPException(404, "no such home")
    r = Room(home_id=hid, name=body.name)
    db.add(r)
    await db.commit()
    return {"id": r.id, "name": r.name}


# ---------------------------------------------------------------- audit
@router.get("/audit")
async def audit(limit: int = 200, db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)))).scalars().all()
    return [{"id": a.id, "at": a.at.isoformat(), "src": a.src, "action": a.action, "target": a.target,
             "detail": a.detail} for a in rows]


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    s = request.app.state.settings
    nodes = (await db.execute(select(func.count()).select_from(Node))).scalar_one()
    online = (await db.execute(select(func.count()).select_from(Node).where(Node.online.is_(True)))).scalar_one()
    return {"broker": request.app.state.bridge.connected.is_set(), "ca": request.app.state.pki.initialized,
            "nodes": nodes, "online": online, "public_host": s.public_host,
            "server_ca": bool(s.provision_server_ca_file), "mqtt_port": s.mqtt_port}
