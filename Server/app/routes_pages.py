import logging
from collections import defaultdict
from datetime import datetime, timezone
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
from .mqtt_bus import get_mqtt_bus
from .node_capabilities import (
    DEFAULT_INDEX_RANGES,
    build_index_options,
    merge_module_lists,
    registry_enabled_modules,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    houses = settings.DEVICE_REGISTRY
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "houses": houses, "title": "UltraLights"},
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    nodes = []
    for house, room, node in registry.iter_nodes():
        house_name = ""
        room_name = ""
        node_name = ""
        if house:
            house_name = (house.get("name") or house.get("id") or "")
        if room:
            room_name = (room.get("name") or room.get("id") or "")
        node_name = node.get("name") or node.get("id") or ""
        node_id = node.get("id") or node_name
        node_modules = registry_enabled_modules(node)
        nodes.append(
            {
                "id": node_id,
                "name": node_name,
                "house": house_name,
                "room": room_name,
                "has_ota": any(m.lower() == "ota" for m in node_modules),
            }
        )
    nodes.sort(key=lambda item: (item["house"].lower(), item["room"].lower(), item["name"].lower()))
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "nodes": nodes,
            "title": "Admin Panel",
            "subtitle": "System status",
            "status_timeout": status_monitor.timeout,
        },
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
async def node_page(request: Request, node_id: str):
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

    registry_modules = registry_enabled_modules(node)
    capability_snapshot = status_monitor.capabilities_for(node_id)
    try:
        capability_snapshot = await get_mqtt_bus().request_status_snapshot(node_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to request status snapshot for %s: %s", node_id, exc)
    live_modules = capability_snapshot.get("modules") or []
    module_indexes = build_index_options(
        capability_snapshot.get("indexes", {}), DEFAULT_INDEX_RANGES
    )
    node_modules = merge_module_lists(live_modules, registry_modules)
    capability_updated_at = capability_snapshot.get("updated_at")
    capability_updated_iso = None
    if isinstance(capability_updated_at, (int, float)):
        capability_updated_iso = datetime.fromtimestamp(
            capability_updated_at, tz=timezone.utc
        ).isoformat()
    module_source = "mqtt" if live_modules else "registry"

    missing = [eff for eff in WS_EFFECTS if eff not in WS_PARAM_DEFS]
    if missing:
        logger.warning("WS_PARAM_DEFS missing entries for: %s", ", ".join(sorted(missing)))

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
            "white_effects": WHITE_EFFECTS,
            "rgb_effects": sorted(RGB_EFFECTS),
            "ws_param_defs": WS_PARAM_DEFS,
            "white_param_defs": WHITE_PARAM_DEFS,
            "rgb_param_defs": RGB_PARAM_DEFS,
            "brightness_limits": brightness_limits.get_limits_for_node(node["id"]),
            "node_modules": node_modules,
            "module_index_options": module_indexes,
            "module_source": module_source,
            "capability_updated_at": capability_updated_at,
            "capability_updated_at_iso": capability_updated_iso,
            "capability_snapshot": capability_snapshot,
        },
    )
