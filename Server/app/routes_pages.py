from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import registry
from .auth.dependencies import get_current_user, require_admin
from .auth.models import House, HouseMembership, User
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
def root(request: Request, session: Session = Depends(get_session)):
    redirect = _redirect_if_authenticated(request, session)
    if redirect:
        return redirect
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = Depends(get_session)):
    redirect = _redirect_if_authenticated(request, session)
    if redirect:
        return redirect
    registry.ensure_house_external_ids(persist=False)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Sign in"},
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


def _collect_admin_nodes(house_id: Optional[str] = None) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for house, room, node in registry.iter_nodes():
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
    }


@router.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, _admin: User = Depends(require_admin)):
    nodes = _collect_admin_nodes()
    return templates.TemplateResponse(
        request,
        "admin.html",
        _admin_template_context(
            request,
            nodes=nodes,
            title="Admin Panel",
            subtitle="System status",
            heading="Admin Panel",
        ),
    )


@router.get("/admin/house/{house_id}", response_class=HTMLResponse)
def admin_house_panel(
    request: Request,
    house_id: str,
    _admin: User = Depends(require_admin),
):
    try:
        house, house_slug = registry.require_house(house_id)
    except KeyError:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"content": "Unknown house"},
            status_code=404,
        )
    house_name = house.get("name") or registry.get_house_slug(house) or house_id
    nodes = _collect_admin_nodes(house_slug)
    public_house_id = registry.get_house_external_id(house)
    rooms: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in house.get("rooms", []) or []:
        if not isinstance(entry, dict):
            continue
        room_id = entry.get("id")
        if not isinstance(room_id, str):
            continue
        clean_id = room_id.strip()
        if not clean_id:
            continue
        if clean_id in seen_ids:
            continue
        seen_ids.add(clean_id)
        name = entry.get("name")
        room_name = str(name).strip() if isinstance(name, str) else clean_id
        node_count = 0
        node_entries = entry.get("nodes")
        if isinstance(node_entries, list):
            for node in node_entries:
                if isinstance(node, dict) and node.get("id"):
                    node_count += 1
        rooms.append({"id": clean_id, "name": room_name, "node_count": node_count})
    rooms.sort(key=lambda item: item["name"].lower())
    return templates.TemplateResponse(
        "admin.html",
        _admin_template_context(
            request,
            nodes=nodes,
            title=f"{house_name} Admin",
            subtitle=f"{house_name} status",
            heading=f"{house_name} Admin",
            description=f"Monitor node heartbeats for {house_name}.",
            status_house_id=public_house_id,
            allow_remove=True,
            house_rooms=rooms,
        ),
    )


@router.get("/house/{house_id}", response_class=HTMLResponse)
def house_page(
    request: Request,
    house_id: str,
    _user: User = Depends(get_current_user),
):
    house = registry.find_house(house_id)
    if not house:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"content": "Unknown house"},
            status_code=404,
        )
    public_house_id = registry.get_house_external_id(house)
    return templates.TemplateResponse(
        request,
        "house.html",
        {
            "house": house,
            "house_public_id": public_house_id,
            "title": house.get("name", house_id),
            "subtitle": house.get("name", house_id),
        },
    )


@router.get("/house/{house_id}/room/{room_id}", response_class=HTMLResponse)
def room_page(
    request: Request,
    house_id: str,
    room_id: str,
    _user: User = Depends(get_current_user),
):
    house, room = registry.find_room(house_id, room_id)
    if not room:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"content": "Unknown room"},
            status_code=404,
        )
    house_slug = registry.get_house_slug(house)
    public_house_id = registry.get_house_external_id(house)
    title = f"{house.get('name', house_id)} - {room.get('name', room_id)}"
    presets = get_room_presets(house_slug, room_id)
    motion_config = _build_motion_config(house_slug, room_id, presets)
    return templates.TemplateResponse(
        request,
        "room.html",
        {
            "house": house,
            "house_public_id": public_house_id,
            "room": room,
            "title": title,
            "subtitle": title,
            "presets": presets,
            "motion_config": motion_config,
        },
    )

@router.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(
    request: Request,
    node_id: str,
    _user: User = Depends(get_current_user),
):
    house, room, node = registry.find_node(node_id)
    if not node:
        return templates.TemplateResponse(
            request,
            "base.html",
            {"content": "Unknown node"},
            status_code=404,
        )
    title = node.get("name", node_id)
    if room and house:
        subtitle = f"{house.get('name', house['id'])} • {room.get('name', room['id'])}"
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
