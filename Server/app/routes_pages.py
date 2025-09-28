import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import node_builder, node_credentials, registry
from .auth.access import AccessPolicy, HouseContext, NodeContext, RoomContext
from .auth.dependencies import get_current_user
from .auth.models import (
    AuditLog,
    House,
    HouseMembership,
    HouseRole,
    NodeRegistration,
    RoomAccess,
    User,
)
from .auth.security import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    clear_session_cookie,
    create_session_token,
    normalize_username,
    set_session_cookie,
    verify_session_token,
)
from .auth.service import record_audit_event
from .auth.throttling import get_login_rate_limiter
from .config import settings
from .database import get_session
from .effects import (
    WS_EFFECTS,
    WHITE_EFFECTS,
    RGB_EFFECTS,
    WS_PARAM_DEFS,
    WHITE_PARAM_DEFS,
    RGB_PARAM_DEFS,
    WS_EFFECT_TIERS,
    WS_EFFECT_TIER_LABELS,
    WS_EFFECT_TIER_ORDER,
)
from .presets import get_room_presets
from .motion import motion_manager
from .motion_schedule import motion_schedule
from .status_monitor import status_monitor
from .routes_api import get_bus
from .brightness_limits import brightness_limits


router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
NODE_MODULE_TEMPLATES = ["ws", "rgb", "white", "motion"]
logger = logging.getLogger(__name__)


def _require_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    try:
        return get_current_user(request, session)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise HTTPException(
                status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"},
            ) from exc
        raise


def _user_default_house_external_id(session: Session, user: User) -> Optional[str]:
    if user.id is None:
        return None

    membership_query = (
        select(House.external_id)
        .join(HouseMembership, HouseMembership.house_id == House.id)
        .where(HouseMembership.user_id == user.id)
        .order_by(HouseMembership.role.desc(), House.id)
    )
    external_id = session.exec(membership_query).first()
    if external_id:
        return str(external_id)

    fallback = session.exec(select(House.external_id).order_by(House.id)).first()
    if fallback:
        return str(fallback)

    registry.ensure_house_external_ids(persist=False)
    first_house = settings.DEVICE_REGISTRY[0] if settings.DEVICE_REGISTRY else None
    if isinstance(first_house, dict):
        external = registry.get_house_external_id(first_house)
        if external:
            return external
    return None


def _default_house_path(session: Session, user: User) -> str:
    external_id = _user_default_house_external_id(session, user)
    if external_id:
        return f"/house/{external_id}"
    return "/admin"


def _redirect_if_authenticated(request: Request, session: Session) -> Optional[RedirectResponse]:
    token_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not token_value:
        return None

    token_data = verify_session_token(token_value)
    if token_data is None:
        return None

    user = session.exec(select(User).where(User.id == token_data.user_id)).first()
    if not user:
        return None

    target = _default_house_path(session, user)
    return RedirectResponse(target, status_code=303)


def _navigation_context(
    policy: AccessPolicy,
    *,
    current_user: User,
    active_house: Optional[str] = None,
    active_room: Optional[str] = None,
) -> Dict[str, Any]:
    houses: List[Dict[str, Any]] = []
    active_entry: Optional[Dict[str, Any]] = None
    try:
        house_entries = list(policy.houses_for_templates())  # type: ignore[attr-defined]
    except AttributeError:
        house_entries = []
    except Exception:
        house_entries = []

    for house in house_entries:
        external_id = registry.get_house_external_id(house)
        try:
            access = policy.get_house_access(external_id)  # type: ignore[attr-defined]
        except AttributeError:
            access = None
        can_manage = bool(access and access.can_manage(current_user))
        raw_name = house.get("name") or house.get("id") or external_id
        house_name = str(raw_name).strip() or external_id
        rooms: List[Dict[str, Any]] = []
        raw_rooms = house.get("rooms") or []
        if isinstance(raw_rooms, list):
            for entry in raw_rooms:
                if not isinstance(entry, dict):
                    continue
                room_id = str(entry.get("id") or "").strip()
                if not room_id:
                    continue
                room_name_value = entry.get("name")
                if isinstance(room_name_value, str):
                    room_name_clean = room_name_value.strip() or room_id
                else:
                    room_name_clean = room_id
                rooms.append(
                    {
                        "id": room_id,
                        "name": room_name_clean,
                        "url": f"/house/{external_id}/room/{room_id}",
                        "is_active": active_house == external_id
                        and active_room == room_id,
                    }
                )
        rooms.sort(key=lambda item: item["name"].lower())
        house_entry = {
            "id": external_id,
            "name": house_name,
            "url": f"/house/{external_id}",
            "rooms": rooms,
            "is_active": active_house == external_id,
            "can_manage": can_manage,
            "admin_url": f"/admin/house/{external_id}" if can_manage else None,
        }
        houses.append(house_entry)
        if house_entry["is_active"]:
            active_entry = house_entry
    try:
        manages_any = bool(policy.manages_any_house())  # type: ignore[attr-defined]
    except AttributeError:
        manages_any = False
    except Exception:
        manages_any = False

    return {
        "nav": {
            "houses": houses,
            "active_house": active_entry,
            "active_house_id": active_entry["id"] if active_entry else None,
            "active_room_id": active_room,
            "show_admin": manages_any,
            "show_server_admin": current_user.server_admin,
            "username": current_user.username,
            "logout_url": "/logout",
        }
    }


@router.get("/", include_in_schema=False)
def root(_request: Request, _user: User = Depends(_require_current_user)):
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = Depends(get_session)):
    redirect = _redirect_if_authenticated(request, session)
    if redirect:
        return redirect
    registry.ensure_house_external_ids(persist=False)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "title": "Sign in"},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    normalized_username = normalize_username(username)
    limiter = get_login_rate_limiter()
    client_host = request.client.host if request.client else "unknown"
    state = limiter.status(client_host)
    if state.blocked:
        record_audit_event(
            session,
            actor=None,
            action="login_rate_limited",
            summary=f"Rate limit hit for {normalized_username}",
            data={
                "username": normalized_username,
                "ip": client_host,
                "retry_after": state.retry_after,
            },
            commit=True,
        )
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "title": "Sign in",
                "error": "Too many login attempts. Try again shortly.",
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        clear_session_cookie(response)
        return response

    user = authenticate_user(session, normalized_username, password)
    if not user:
        failure_state = limiter.register_failure(client_host)
        record_audit_event(
            session,
            actor=None,
            action="login_failed",
            summary=f"Failed login for {normalized_username}",
            data={
                "username": normalized_username,
                "ip": client_host,
                "rate_limited": failure_state.blocked,
                "retry_after": failure_state.retry_after,
            },
            commit=True,
        )
        error_message = (
            "Too many login attempts. Try again shortly."
            if failure_state.blocked
            else "Invalid username or password"
        )
        status_code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if failure_state.blocked
            else status.HTTP_400_BAD_REQUEST
        )
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "title": "Sign in",
                "error": error_message,
            },
            status_code=status_code,
        )
        clear_session_cookie(response)
        return response

    limiter.register_success(client_host)
    record_audit_event(
        session,
        actor=user,
        action="login_success",
        summary=f"User {user.username} signed in",
        data={"ip": client_host},
        commit=True,
    )
    redirect = RedirectResponse(_default_house_path(session, user), status_code=303)
    token = create_session_token(user)
    set_session_cookie(redirect, token)
    return redirect


@router.get("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    actor: Optional[User] = None
    token_value = request.cookies.get(SESSION_COOKIE_NAME)
    if token_value:
        token_data = verify_session_token(token_value)
        if token_data is not None:
            actor = session.exec(select(User).where(User.id == token_data.user_id)).first()

    record_audit_event(
        session,
        actor=actor,
        action="logout",
        summary=f"User {actor.username if actor else 'unknown'} signed out",
        data={"ip": request.client.host if request.client else "unknown"},
        commit=True,
    )
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(policy, current_user=current_user)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "title": "Dashboard",
            "subtitle": None,
            "current_user": current_user,
            **nav_context,
        },
    )


def _build_motion_config(
    house_id: str, room_id: str, presets: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    room_key = (house_id, room_id)
    motion_manager.ensure_room_loaded(house_id, room_id)
    sensor_entry = motion_manager.room_sensors.get(room_key)
    sensor_nodes: List[Dict[str, Any]] = []
    if sensor_entry:
        for node_id, node_info in sensor_entry.get("nodes", {}).items():
            raw_config = node_info.get("config")
            if not raw_config:
                raw_config = motion_manager.config.get(node_id, {})
            node_config = raw_config or {}
            sensors = node_info.get("sensors") or {}
            if not node_config and not sensors:
                continue
            node_name = node_info.get("node_name")
            if not node_name:
                _, _, node = registry.find_node(node_id)
                if node:
                    node_name = node.get("name") or node_id
                else:
                    node_name = node_id
            duration = int(node_config.get("duration", 30)) if node_config else 30
            enabled = bool(node_config.get("enabled", True)) if node_config else True
            pir_enabled = None
            if node_config is not None and "pir_enabled" in node_config:
                pir_enabled = bool(node_config.get("pir_enabled"))
                if pir_enabled is False and not sensors:
                    continue
            node_entry: Dict[str, Any] = {
                "node_id": node_id,
                "node_name": node_name,
                "enabled": enabled,
                "duration": duration,
            }
            if pir_enabled is not None:
                node_entry["pir_enabled"] = pir_enabled
            sensor_nodes.append(node_entry)
    if not sensor_nodes:
        return None
    sensor_nodes.sort(key=lambda item: item["node_name"].lower())
    schedule = motion_schedule.get_schedule_or_default(house_id, room_id)
    palette = [
        "#f97316",
        "#38bdf8",
        "#a855f7",
        "#22c55e",
        "#eab308",
        "#f43f5e",
        "#6366f1",
        "#14b8a6",
        "#ec4899",
        "#facc15",
    ]
    preset_colors: Dict[str, str] = {}
    preset_names: Dict[str, str] = {}
    for idx, preset in enumerate(presets):
        preset_id = preset.get("id")
        if not preset_id:
            continue
        preset_id = str(preset_id)
        preset_colors[preset_id] = palette[idx % len(palette)]
        preset_names[preset_id] = preset.get("name", preset_id)
    for preset_id in schedule:
        if preset_id and preset_id not in preset_colors:
            preset_colors[preset_id] = palette[len(preset_colors) % len(palette)]
            preset_names.setdefault(preset_id, preset_id)
    stored_colors = motion_schedule.get_room_colors(house_id, room_id)
    for preset_id, color in stored_colors.items():
        if not color:
            continue
        preset_colors[preset_id] = color
        preset_names.setdefault(preset_id, preset_id)
    legend = [
        {
            "id": preset_id,
            "name": preset_names.get(preset_id, preset_id),
            "color": color,
        }
        for preset_id, color in preset_colors.items()
    ]
    return {
        "schedule": schedule,
        "slot_minutes": motion_schedule.slot_minutes,
        "preset_colors": preset_colors,
        "preset_names": preset_names,
        "legend": legend,
        "no_motion_color": "#1f2937",
        "sensors": sensor_nodes,
    }


def _collect_admin_nodes(
    policy: AccessPolicy, house_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for house, room, node in registry.iter_nodes():
        if not house:
            continue
        external_id = registry.get_house_external_id(house)
        access = policy.get_house_access(external_id)
        if access is None or not access.can_manage(policy.user):
            continue
        slug = registry.get_house_slug(house)
        if house_id and slug != house_id:
            continue
        house_name = str(house.get("name") or house.get("id") or "")
        room_name = ""
        if room:
            room_name = str(room.get("name") or room.get("id") or "")
        node_name = node.get("name") or node.get("id") or ""
        node_id = node.get("id") or node_name
        nodes.append(
            {
                "id": node_id,
                "name": node_name,
                "house": house_name,
                "room": room_name,
                "room_id": room.get("id") if isinstance(room, dict) else None,
                "has_ota": "ota" in (node.get("modules") or []),
                "is_unassigned": False,
            }
        )

    for house, node in registry.iter_unassigned_nodes():
        if not isinstance(house, dict):
            continue
        slug = registry.get_house_slug(house)
        if house_id and slug != house_id:
            continue
        external_id = registry.get_house_external_id(house)
        access = policy.get_house_access(external_id)
        if access is None or not access.can_manage(policy.user):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        node_name = node.get("name") or node_id
        house_name = str(house.get("name") or house.get("id") or "")
        nodes.append(
            {
                "id": node_id,
                "name": node_name,
                "house": house_name,
                "room": "Unassigned",
                "room_id": None,
                "has_ota": "ota" in (node.get("modules") or []),
                "is_unassigned": True,
            }
        )
    nodes.sort(key=lambda item: (item["house"].lower(), item["room"].lower(), item["name"].lower()))
    return nodes


def _request_status_updates(node_entries: Iterable[Dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in node_entries:
        node_id = entry.get("id") if isinstance(entry, dict) else None
        if node_id is None:
            continue
        if not isinstance(node_id, str):
            node_id = str(node_id)
        clean_id = node_id.strip()
        if clean_id:
            seen.add(clean_id)
    if not seen:
        return

    try:
        bus = get_bus()
    except Exception:  # pragma: no cover - best-effort logging for unexpected failure
        logger.exception("Failed to request admin status refresh: MQTT bus unavailable")
        return

    for node_id in seen:
        try:
            bus.status_request(node_id)
        except Exception:  # pragma: no cover - best-effort logging for publish issues
            logger.exception("Failed to request status update for node %s", node_id)


def _admin_template_context(
    request: Request,
    *,
    nodes: List[Dict[str, Any]],
    title: str,
    subtitle: str,
    heading: Optional[str] = None,
    description: Optional[str] = None,
    status_house_id: Optional[str] = None,
    allow_remove: bool = False,
    house_rooms: Optional[List[Dict[str, Any]]] = None,
    house_node_assignments: Optional[List[Dict[str, Any]]] = None,
    house_memberships: Optional[List[Dict[str, Any]]] = None,
    house_member_options: Optional[List[Dict[str, Any]]] = None,
    house_member_roles: Optional[List[Dict[str, str]]] = None,
    house_member_manage_allowed: bool = False,
    house_admin_external_id: Optional[str] = None,
):
    return {
        "request": request,
        "nodes": nodes,
        "title": title,
        "subtitle": subtitle,
        "heading": heading or title,
        "description": description
        or "Monitor node heartbeats and trigger OTA checks.",
        "status_timeout": status_monitor.timeout,
        "status_house_id": status_house_id,
        "allow_remove": allow_remove,
        "house_rooms": house_rooms,
        "house_node_assignments": house_node_assignments,
        "house_memberships": house_memberships,
        "house_member_options": house_member_options,
        "house_member_roles": house_member_roles,
        "house_member_manage_allowed": house_member_manage_allowed,
        "house_admin_external_id": house_admin_external_id,
    }


@router.get("/admin", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(policy, current_user=current_user)
    if not policy.manages_any_house():
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    nodes = _collect_admin_nodes(policy)
    _request_status_updates(nodes)
    return templates.TemplateResponse(
        request,
        "admin.html",
        _admin_template_context(
            request,
            nodes=nodes,
            title="Admin Panel",
            subtitle="System status",
            heading="Admin Panel",
            allow_remove=current_user.server_admin,
        )
        | nav_context,
    )


@router.get("/server-admin", response_class=HTMLResponse)
def server_admin_panel(
    request: Request,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(policy, current_user=current_user)
    if not current_user.server_admin:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    registry.ensure_house_external_ids(persist=False)
    houses_summary: List[Dict[str, Any]] = []
    total_nodes = 0

    for house in settings.DEVICE_REGISTRY:
        if not isinstance(house, dict):
            continue
        external_id = registry.get_house_external_id(house)
        slug = registry.get_house_slug(house)
        name_value = house.get("name") or house.get("id") or external_id
        house_name = str(name_value)
        room_entries = house.get("rooms") or []
        node_entries: List[Dict[str, Any]] = []
        room_count = 0
        for room in room_entries:
            if not isinstance(room, dict):
                continue
            room_count += 1
            room_name = str(room.get("name") or room.get("id") or "").strip()
            if not room_name:
                room_name = str(room.get("id") or "Room")
            node_list = room.get("nodes") or []
            for node in node_list:
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("id") or "").strip()
                if not node_id:
                    continue
                node_name = str(node.get("name") or node_id)
                node_kind = str(node.get("kind") or "").strip() or None
                node_entries.append(
                    {
                        "id": node_id,
                        "name": node_name,
                        "room": room_name,
                        "kind": node_kind,
                    }
                )
        node_entries.sort(key=lambda item: item["name"].lower())
        node_count = len(node_entries)
        total_nodes += node_count
        houses_summary.append(
            {
                "name": house_name,
                "external_id": external_id,
                "slug": slug,
                "room_count": room_count,
                "node_count": node_count,
                "nodes": node_entries,
            }
        )

    user_rows = session.exec(select(User).order_by(User.username)).all()
    membership_rows = session.exec(
        select(HouseMembership, House)
        .join(House, House.id == HouseMembership.house_id)
    ).all()
    memberships_by_user: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for membership, house in membership_rows:
        memberships_by_user[membership.user_id].append(
            {
                "house_name": house.display_name,
                "house_id": house.external_id,
                "role": membership.role.value,
            }
        )
    accounts_summary: List[Dict[str, Any]] = []
    user_lookup = {user.id: user.username for user in user_rows if user.id is not None}
    for user in user_rows:
        assignments = memberships_by_user.get(user.id or -1, [])
        assignments.sort(key=lambda item: item["house_name"].lower())
        accounts_summary.append(
            {
                "id": user.id,
                "username": user.username,
                "server_admin": user.server_admin,
                "assignments": assignments,
            }
        )

    audit_rows = session.exec(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
    ).all()
    audit_entries = [
        {
            "id": entry.id,
            "action": entry.action,
            "summary": entry.summary,
            "data": entry.data,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "actor": user_lookup.get(entry.actor_id),
        }
        for entry in audit_rows
    ]

    available_regs = node_credentials.list_available_registrations(session)
    assigned_regs = node_credentials.list_assigned_registrations(session)

    def _factory_summary(registration: NodeRegistration) -> Dict[str, Any]:
        metadata = registration.hardware_metadata or {}
        board = None
        if isinstance(metadata, dict):
            board_value = metadata.get("board")
            if isinstance(board_value, str):
                board = board_value
        return {
            "nodeId": registration.node_id,
            "downloadId": registration.download_id,
            "displayName": registration.display_name,
            "board": board,
            "assigned": registration.assigned_at is not None,
            "house": registration.house_slug,
            "room": registration.room_id,
        }

    node_factory_context = {
        "board_options": sorted(node_builder.SUPPORTED_TARGETS.keys()),
        "available": [_factory_summary(reg) for reg in available_regs],
        "assigned": [_factory_summary(reg) for reg in assigned_regs],
    }

    return templates.TemplateResponse(
        request,
        "server_admin.html",
        {
            "request": request,
            "title": "Server Administration",
            "subtitle": "Global controls",
            "houses": houses_summary,
            "house_options": [
                {"id": house["external_id"], "name": house["name"]}
                for house in houses_summary
            ],
            "accounts": accounts_summary,
            "audit_entries": audit_entries,
            "total_nodes": total_nodes,
            "node_factory": node_factory_context,
            **nav_context,
        },
    )


@router.get("/admin/house/{house_id}", response_class=HTMLResponse)
def admin_house_panel(
    request: Request,
    house_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(
        policy,
        current_user=current_user,
        active_house=house_id,
    )
    try:
        house_ctx = policy.ensure_house(house_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown house", **nav_context},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if not house_ctx.access.can_manage(current_user):
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    nodes = _collect_admin_nodes(policy, house_ctx.slug)
    _request_status_updates(nodes)
    house_name = house_ctx.original.get("name") or house_ctx.slug or house_id
    rooms: List[Dict[str, Any]] = []
    for entry in house_ctx.original.get("rooms", []) or []:
        if not isinstance(entry, dict):
            continue
        room_id = str(entry.get("id") or "").strip()
        if not room_id:
            continue
        if not house_ctx.access.can_view_room(room_id):
            continue
        name = entry.get("name")
        room_name = str(name).strip() if isinstance(name, str) else room_id
        node_count = 0
        node_entries = entry.get("nodes")
        if isinstance(node_entries, list):
            for node in node_entries:
                if isinstance(node, dict) and node.get("id"):
                    node_count += 1
        rooms.append({"id": room_id, "name": room_name, "node_count": node_count})
    rooms.sort(key=lambda item: item["name"].lower())
    room_lookup = {room["id"]: room["name"] for room in rooms}
    available_room_options = [{"id": room["id"], "name": room["name"]} for room in rooms]
    memberships: List[Dict[str, Any]] = []
    role_options = [
        {"value": HouseRole.GUEST.value, "label": "Guest"},
        {"value": HouseRole.ADMIN.value, "label": "House Admin"},
    ]
    house_db = house_ctx.access.house
    if house_db is None:
        house_db = session.exec(
            select(House).where(House.external_id == house_ctx.external_id)
        ).first()
    if house_db is not None:
        rows = session.exec(
            select(HouseMembership, User)
            .join(User, User.id == HouseMembership.user_id)
            .where(HouseMembership.house_id == house_db.id)
            .order_by(User.username)
        ).all()
        membership_ids = [membership.id for membership, _ in rows if membership.id is not None]
        access_map: Dict[int, set[str]] = {}
        if membership_ids:
            for access in session.exec(
                select(RoomAccess).where(RoomAccess.membership_id.in_(membership_ids))
            ):
                membership_id = access.membership_id
                room_id = str(access.room_id)
                if membership_id is None or not room_id:
                    continue
                access_map.setdefault(membership_id, set()).add(room_id)
        for membership, user in rows:
            assigned_ids = sorted(
                access_map.get(membership.id, set()),
                key=lambda rid: room_lookup.get(rid, rid).lower(),
            )
            memberships.append(
                {
                    "id": membership.id,
                    "user_id": membership.user_id,
                    "username": user.username,
                    "role": membership.role.value,
                    "role_label": "House Admin"
                    if membership.role == HouseRole.ADMIN
                    else "Guest",
                    "server_admin": user.server_admin,
                    "rooms": [
                        {"id": rid, "name": room_lookup.get(rid, rid)}
                        for rid in assigned_ids
                    ],
                }
            )
    node_assignment_options = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "roomId": entry.get("room_id"),
        }
        for entry in nodes
    ]

    return templates.TemplateResponse(
        request,
        "admin.html",
        _admin_template_context(
            request,
            nodes=nodes,
            title=f"{house_name} Admin",
            subtitle=f"{house_name} status",
            heading=f"{house_name} Admin",
            description=f"Monitor node heartbeats for {house_name}.",
            status_house_id=house_ctx.external_id,
            allow_remove=True,
            house_rooms=rooms,
            house_node_assignments=node_assignment_options,
            house_memberships=memberships,
            house_member_options=available_room_options,
            house_member_roles=role_options,
            house_member_manage_allowed=house_ctx.access.can_manage(current_user),
            house_admin_external_id=house_ctx.external_id,
        )
        | nav_context,
    )


@router.get("/house/{house_id}", response_class=HTMLResponse)
def house_page(
    request: Request,
    house_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(
        policy,
        current_user=current_user,
        active_house=house_id,
    )
    try:
        house_ctx = policy.ensure_house(house_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown house", **nav_context},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    can_manage = house_ctx.access.can_manage(current_user)
    public_house_id = house_ctx.external_id
    pending_nodes: List[Dict[str, Any]] = []
    room_options: List[Dict[str, str]] = []

    rooms = house_ctx.filtered.get("rooms")
    if isinstance(rooms, list):
        for entry in rooms:
            if not isinstance(entry, dict):
                continue
            room_id = str(entry.get("id") or "").strip()
            if not room_id:
                continue
            room_name_value = entry.get("name")
            room_name = (
                str(room_name_value).strip()
                if isinstance(room_name_value, str)
                else room_id
            )
            room_options.append({"id": room_id, "name": room_name})

    if can_manage:
        pending_regs = node_credentials.list_pending_registrations_for_user(
            session, current_user.id
        )
        for registration in pending_regs:
            pending_nodes.append(
                {
                    "id": registration.node_id,
                    "name": registration.display_name or registration.node_id,
                    "downloadId": registration.download_id,
                }
            )

    return templates.TemplateResponse(
        request,
        "house.html",
        {
            "request": request,
            "house": house_ctx.filtered,
            "house_public_id": public_house_id,
            "title": house_ctx.filtered.get("name", house_id),
            "subtitle": house_ctx.filtered.get("name", house_id),
            "can_manage_house": can_manage,
            "house_pending_nodes": pending_nodes,
            "house_room_options": room_options,
            **nav_context,
        },
    )


@router.get("/house/{house_id}/room/{room_id}", response_class=HTMLResponse)
def room_page(
    request: Request,
    house_id: str,
    room_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(
        policy,
        current_user=current_user,
        active_house=house_id,
        active_room=room_id,
    )
    try:
        room_ctx = policy.ensure_room(house_id, room_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown room", **nav_context},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    house_slug = room_ctx.house.slug
    public_house_id = room_ctx.house.external_id
    room_name = room_ctx.filtered_room.get("name", room_id)
    house_name = room_ctx.house.filtered.get("name", house_id)
    title = f"{house_name} - {room_name}"
    presets = get_room_presets(house_slug, room_id)
    motion_config = _build_motion_config(house_slug, room_id, presets)
    can_manage = room_ctx.house.access.can_manage(current_user)
    return templates.TemplateResponse(
        request,
        "room.html",
        {
            "request": request,
            "house": room_ctx.house.filtered,
            "house_public_id": public_house_id,
            "room": room_ctx.filtered_room,
            "title": title,
            "subtitle": title,
            "presets": presets,
            "motion_config": motion_config,
            "can_manage_room": can_manage,
            **nav_context,
        },
    )

@router.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(
    request: Request,
    node_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    nav_context = _navigation_context(policy, current_user=current_user)
    try:
        node_ctx = policy.ensure_node(node_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown node", **nav_context},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden", **nav_context},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    room_identifier = str(node_ctx.room.room.get("id") or "").strip()
    nav_context = _navigation_context(
        policy,
        current_user=current_user,
        active_house=node_ctx.room.house.external_id,
        active_room=room_identifier or None,
    )
    node = node_ctx.node
    room = node_ctx.room.room
    house = node_ctx.room.house.original
    title = node.get("name", node_id)
    if room and house:
        subtitle = f"{house.get('name', house.get('id'))} • {room.get('name', room.get('id'))}"
    else:
        subtitle = None

    missing = [eff for eff in WS_EFFECTS if eff not in WS_PARAM_DEFS]
    if missing:
        import logging
        logging.warning("WS_PARAM_DEFS missing entries for: %s", ", ".join(sorted(missing)))

    tier_groups: dict[str, list[str]] = defaultdict(list)
    for eff in sorted(WS_EFFECTS):
        tier = WS_EFFECT_TIERS.get(eff, "standard")
        tier_groups[tier].append(eff)

    ws_effect_groups = [
        {
            "key": tier,
            "label": WS_EFFECT_TIER_LABELS.get(tier, tier.replace("_", " ").title()),
            "effects": names,
        }
        for tier, names in sorted(
            tier_groups.items(),
            key=lambda item: (WS_EFFECT_TIER_ORDER.get(item[0], 99), item[0]),
        )
    ]

    status_info = status_monitor.status_for(node["id"])
    status_initial_online = bool(status_info.get("online"))

    return templates.TemplateResponse(
        request,
        "node.html",
        {
            "request": request,
            "node": node,
            "title": title,
            "subtitle": subtitle,
            "ws_effects": WS_EFFECTS,
            "ws_effect_groups": ws_effect_groups,
            "ws_effect_tiers": WS_EFFECT_TIERS,
            "white_effects": sorted(WHITE_EFFECTS),
            "rgb_effects": sorted(RGB_EFFECTS),
            "ws_param_defs": WS_PARAM_DEFS,
            "white_param_defs": WHITE_PARAM_DEFS,
            "rgb_param_defs": RGB_PARAM_DEFS,
            "status_timeout": status_monitor.timeout,
            "status_initial_online": status_initial_online,
            "brightness_limits": brightness_limits.get_limits_for_node(node["id"]),
            "module_templates": NODE_MODULE_TEMPLATES,
            **nav_context,
        },
    )
