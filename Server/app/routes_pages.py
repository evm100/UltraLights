from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import registry
from .auth.access import AccessPolicy, HouseContext, NodeContext, RoomContext
from .auth.dependencies import get_current_user
from .auth.models import House, HouseMembership, HouseRole, RoomAccess, User
from .auth.security import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    clear_session_cookie,
    create_session_token,
    set_session_cookie,
    verify_session_token,
)
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
from .brightness_limits import brightness_limits


router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
NODE_MODULE_TEMPLATES = ["ws", "rgb", "white", "ota", "motion"]


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
    user = authenticate_user(session, username, password)
    if not user:
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "title": "Sign in",
                "error": "Invalid username or password",
            },
            status_code=400,
        )
        clear_session_cookie(response)
        return response

    redirect = RedirectResponse(_default_house_path(session, user), status_code=303)
    token = create_session_token(user)
    set_session_cookie(redirect, token)
    return redirect


@router.get("/logout")
def logout():
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
    houses = policy.houses_for_templates()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "houses": houses,
            "title": "Dashboard",
            "subtitle": None,
            "show_admin": policy.manages_any_house(),
            "can_all_off": current_user.server_admin,
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
        if house_id:
            slug = registry.get_house_slug(house) if house else None
            if slug != house_id:
                continue
        house_name = ""
        room_name = ""
        node_name = ""
        if house:
            house_name = (house.get("name") or house.get("id") or "")
        if room:
            room_name = (room.get("name") or room.get("id") or "")
        node_name = node.get("name") or node.get("id") or ""
        node_id = node.get("id") or node_name
        nodes.append(
            {
                "id": node_id,
                "name": node_name,
                "house": house_name,
                "room": room_name,
                "has_ota": "ota" in (node.get("modules") or []),
            }
        )
    nodes.sort(key=lambda item: (item["house"].lower(), item["room"].lower(), item["name"].lower()))
    return nodes


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
    if not policy.manages_any_house():
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    nodes = _collect_admin_nodes(policy)
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
        ),
    )


@router.get("/admin/house/{house_id}", response_class=HTMLResponse)
def admin_house_panel(
    request: Request,
    house_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    try:
        house_ctx = policy.ensure_house(house_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown house"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
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
            house_memberships=memberships,
            house_member_options=available_room_options,
            house_member_roles=role_options,
            house_member_manage_allowed=current_user.server_admin,
            house_admin_external_id=house_ctx.external_id,
        ),
    )


@router.get("/house/{house_id}", response_class=HTMLResponse)
def house_page(
    request: Request,
    house_id: str,
    current_user: User = Depends(_require_current_user),
    session: Session = Depends(get_session),
):
    policy = AccessPolicy.from_session(session, current_user)
    try:
        house_ctx = policy.ensure_house(house_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown house"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    can_manage = house_ctx.access.can_manage(current_user)
    public_house_id = house_ctx.external_id
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
    try:
        room_ctx = policy.ensure_room(house_id, room_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown room"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
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
    try:
        node_ctx = policy.ensure_node(node_id)
    except LookupError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Unknown node"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except PermissionError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"request": request, "content": "Forbidden"},
            status_code=status.HTTP_403_FORBIDDEN,
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
        },
    )
