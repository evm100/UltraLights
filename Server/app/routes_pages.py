from collections import defaultdict

from typing import Any, Dict, List, Optional

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import settings
from . import registry
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
from .motion import motion_manager, SPECIAL_ROOM_PRESETS
from .motion_schedule import motion_schedule
from .status_monitor import status_monitor
from .brightness_limits import brightness_limits


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
NODE_MODULE_TEMPLATES = ["ws", "rgb", "white", "ota", "motion"]


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    houses = settings.DEVICE_REGISTRY
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "houses": houses, "title": "UltraLights"},
    )


def _collect_admin_nodes(house_id: Optional[str] = None) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for house, room, node in registry.iter_nodes():
        if house_id and house and house.get("id") != house_id:
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
    }


@router.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    nodes = _collect_admin_nodes()
    return templates.TemplateResponse(
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
def admin_house_panel(request: Request, house_id: str):
    house = registry.find_house(house_id)
    if not house:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Unknown house"},
            status_code=404,
        )
    house_name = house.get("name") or house.get("id") or house_id
    nodes = _collect_admin_nodes(house_id)
    return templates.TemplateResponse(
        "admin.html",
        _admin_template_context(
            request,
            nodes=nodes,
            title=f"{house_name} Admin",
            subtitle=f"{house_name} status",
            heading=f"{house_name} Admin",
            description=f"Monitor node heartbeats for {house_name}.",
            status_house_id=house_id,
            allow_remove=True,
        ),
    )


@router.get("/house/{house_id}", response_class=HTMLResponse)
def house_page(request: Request, house_id: str):
    house = registry.find_house(house_id)
    if not house:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Unknown house"},
            status_code=404,
        )
    return templates.TemplateResponse(
        "house.html",
        {
            "request": request,
            "house": house,
            "title": house.get("name", house_id),
            "subtitle": house.get("name", house_id),
        },
    )


@router.get("/house/{house_id}/room/{room_id}", response_class=HTMLResponse)
def room_page(request: Request, house_id: str, room_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not room:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Unknown room"},
            status_code=404,
        )
    title = f"{house.get('name', house_id)} - {room.get('name', room_id)}"
    presets = get_room_presets(house_id, room_id)
    motion_config = None
    special = SPECIAL_ROOM_PRESETS.get((house_id, room_id))
    if special:
        node_id = special.get("node")
        if node_id:
            cfg = motion_manager.config.get(node_id, {})
            default_preset = special.get("on")
            schedule = motion_schedule.get_schedule_or_default(
                house_id, room_id, default=default_preset
            )
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
            preset_colors = {}
            preset_names = {}
            for idx, preset in enumerate(presets):
                preset_colors[preset["id"]] = palette[idx % len(palette)]
                preset_names[preset["id"]] = preset.get("name", preset["id"])
            if default_preset and default_preset not in preset_colors:
                preset_colors[default_preset] = palette[len(preset_colors) % len(palette)]
                preset_names.setdefault(default_preset, default_preset)
            for preset_id in schedule:
                if preset_id and preset_id not in preset_colors:
                    preset_colors[preset_id] = palette[len(preset_colors) % len(palette)]
                    preset_names.setdefault(preset_id, preset_id)
            legend = [
                {
                    "id": preset_id,
                    "name": preset_names.get(preset_id, preset_id),
                    "color": color,
                }
                for preset_id, color in preset_colors.items()
            ]
            motion_config = {
                "duration": int(cfg.get("duration", 30)),
                "node_id": node_id,
                "schedule": schedule,
                "slot_minutes": motion_schedule.slot_minutes,
                "preset_colors": preset_colors,
                "preset_names": preset_names,
                "legend": legend,
                "no_motion_color": "#1f2937",
            }
    return templates.TemplateResponse(
        "room.html",
        {
            "request": request,
            "house": house,
            "room": room,
            "title": title,
            "subtitle": title,
            "presets": presets,
            "motion_config": motion_config,
        },
    )

@router.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(request: Request, node_id: str):
    house, room, node = registry.find_node(node_id)
    if not node:
        return templates.TemplateResponse(
            "base.html", {"request": request, "content": "Unknown node"}, status_code=404
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
