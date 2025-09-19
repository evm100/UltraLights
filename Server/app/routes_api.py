from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException

from .mqtt_bus import MqttBus, get_mqtt_bus
from . import registry
from .effects import WS_EFFECTS, WHITE_EFFECTS, RGB_EFFECTS
from .presets import get_preset, apply_preset, get_room_presets
from .motion import motion_manager, SPECIAL_ROOM_PRESETS
from .motion_schedule import motion_schedule
from .status_monitor import status_monitor
from .brightness_limits import brightness_limits
from .node_capabilities import (
    DEFAULT_INDEX_RANGES,
    build_index_options,
    merge_module_lists,
    registry_enabled_modules,
)

router = APIRouter()


def get_bus() -> MqttBus:
    return get_mqtt_bus()

def _valid_node(node_id: str) -> Dict[str, Any]:
    _, _, node = registry.find_node(node_id)
    if node:
        return node
    raise HTTPException(404, "Unknown node id")


def _node_state(node_id: str) -> tuple[Dict[str, Any], list[str], dict[str, list[int]]]:
    node = _valid_node(node_id)
    snapshot = status_monitor.capabilities_for(node_id)
    live_modules = snapshot.get("modules") or []
    modules = merge_module_lists(live_modules, registry_enabled_modules(node))
    indexes = build_index_options(snapshot.get("indexes", {}), DEFAULT_INDEX_RANGES)
    return node, modules, indexes

@router.post("/api/all-off")
def api_all_off():
    get_bus().all_off()
    return {"ok": True}

@router.post("/api/house/{house_id}/rooms")
def api_add_room(house_id: str, payload: Dict[str, str]):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "missing name")
    try:
        room = registry.add_room(house_id, name)
    except KeyError:
        raise HTTPException(404, "Unknown house")
    return {"ok": True, "room": room}

@router.post("/api/house/{house_id}/room/{room_id}/nodes")
def api_add_node(house_id: str, room_id: str, payload: Dict[str, Any]):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "missing name")
    kind = str(payload.get("kind", "ultranode"))
    modules = payload.get("modules")
    try:
        node = registry.add_node(house_id, room_id, name, kind, modules)
    except KeyError:
        raise HTTPException(404, "Unknown room")
    return {"ok": True, "node": node}


@router.post("/api/house/{house_id}/room/{room_id}/preset/{preset_id}")
def api_apply_preset(house_id: str, room_id: str, preset_id: str):
    preset = get_preset(house_id, room_id, preset_id)
    if not preset:
        raise HTTPException(404, "Unknown preset")
    apply_preset(get_bus(), preset)
    return {"ok": True}


@router.post("/api/house/{house_id}/room/{room_id}/motion-schedule")
def api_set_motion_schedule(house_id: str, room_id: str, payload: Dict[str, Any]):
    if (house_id, room_id) not in SPECIAL_ROOM_PRESETS:
        raise HTTPException(404, "Motion schedule not supported for this room")
    schedule = payload.get("schedule")
    if not isinstance(schedule, list):
        raise HTTPException(400, "invalid schedule")
    if len(schedule) != motion_schedule.slot_count:
        raise HTTPException(400, "invalid schedule length")
    valid_presets = {p["id"] for p in get_room_presets(house_id, room_id)}
    default_preset = SPECIAL_ROOM_PRESETS[(house_id, room_id)].get("on")
    if default_preset:
        valid_presets.add(default_preset)
    clean: List[Optional[str]] = []
    for value in schedule:
        if value in (None, "", "none"):
            clean.append(None)
            continue
        if not isinstance(value, str):
            raise HTTPException(400, "invalid preset value")
        if value not in valid_presets:
            raise HTTPException(400, f"unknown preset: {value}")
        clean.append(value)
    stored = motion_schedule.set_schedule(house_id, room_id, clean)
    return {"ok": True, "schedule": stored}

# ---- Node command APIs -------------------------------------------------

@router.post("/api/node/{node_id}/ws/set")
def api_ws_set(node_id: str, payload: Dict[str, Any]):
    _, modules, indexes = _node_state(node_id)
    module_lookup = {m.lower(): m for m in modules}
    if "ws" not in module_lookup:
        raise HTTPException(404, "WS module not available")
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    valid_strips = indexes.get("ws", list(DEFAULT_INDEX_RANGES["ws"]))
    if strip not in valid_strips:
        raise HTTPException(400, "invalid strip")
    effect = str(payload.get("effect", "")).strip()
    if effect not in WS_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not isinstance(params, list):
            raise HTTPException(400, "invalid params")
        clean: list[object] = []
        for p in params:
            if isinstance(p, (int, float)):
                clean.append(float(p))
            elif isinstance(p, str):
                clean.append(p)
            else:
                raise HTTPException(400, "invalid params")
        params = clean
    get_bus().ws_set(node_id, strip, effect, brightness, params)
    return {"ok": True}

@router.post("/api/node/{node_id}/white/set")
def api_white_set(node_id: str, payload: Dict[str, Any]):
    _, modules, indexes = _node_state(node_id)
    module_lookup = {m.lower(): m for m in modules}
    if "white" not in module_lookup:
        raise HTTPException(404, "White module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    valid_channels = indexes.get("white", list(DEFAULT_INDEX_RANGES["white"]))
    if channel not in valid_channels:
        raise HTTPException(400, "invalid channel")
    effect = str(payload.get("effect", "")).strip()
    if effect not in WHITE_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not (
            isinstance(params, list)
            and all(isinstance(p, (int, float)) for p in params)
        ):
            raise HTTPException(400, "invalid params")
        params = [float(p) for p in params]
    get_bus().white_set(node_id, channel, effect, brightness, params)
    return {"ok": True}


@router.post("/api/node/{node_id}/rgb/set")
def api_rgb_set(node_id: str, payload: Dict[str, Any]):
    _, modules, indexes = _node_state(node_id)
    module_lookup = {m.lower(): m for m in modules}
    if "rgb" not in module_lookup:
        raise HTTPException(404, "RGB module not available")
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    valid_strips = indexes.get("rgb", list(DEFAULT_INDEX_RANGES["rgb"]))
    if strip not in valid_strips:
        raise HTTPException(400, "invalid strip")
    effect = str(payload.get("effect", "")).strip()
    if effect not in RGB_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not isinstance(params, list):
            raise HTTPException(400, "invalid params")
        if len(params) < 3:
            raise HTTPException(400, "invalid params")
        clean: List[int] = []
        for value in params:
            if not isinstance(value, (int, float)):
                raise HTTPException(400, "invalid params")
            v = int(value)
            if not 0 <= v <= 255:
                raise HTTPException(400, "invalid params")
            clean.append(v)
        params = clean
    get_bus().rgb_set(node_id, strip, effect, brightness, params)
    return {"ok": True}


@router.post("/api/node/{node_id}/{module}/brightness-limit")
def api_set_brightness_limit(node_id: str, module: str, payload: Dict[str, Any]):
    _, modules, indexes = _node_state(node_id)
    module_key = str(module).lower()
    if module_key not in {"ws", "white", "rgb"}:
        raise HTTPException(404, "unsupported module")
    module_lookup = {m.lower(): m for m in modules}
    if module_key not in module_lookup:
        raise HTTPException(404, "module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    valid_channels = indexes.get(module_key, list(DEFAULT_INDEX_RANGES[module_key]))
    if channel not in valid_channels:
        raise HTTPException(400, "invalid channel")
    limit = payload.get("limit")
    if limit is None:
        brightness_limits.set_limit(node_id, module_key, channel, None)
        return {"ok": True, "limit": None}
    try:
        value = int(limit)
    except Exception:
        raise HTTPException(400, "invalid limit")
    if not 0 <= value <= 255:
        raise HTTPException(400, "invalid limit")
    stored = brightness_limits.set_limit(node_id, module_key, channel, value)
    return {"ok": True, "limit": stored}

@router.post("/api/node/{node_id}/sensor/cooldown")
def api_sensor_cooldown(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    try:
        seconds = int(payload.get("seconds"))
    except Exception:
        raise HTTPException(400, "invalid seconds")
    if not 10 <= seconds <= 3600:
        raise HTTPException(400, "invalid seconds")
    get_bus().sensor_cooldown(node_id, seconds)
    return {"ok": True, "seconds": seconds}

@router.post("/api/node/{node_id}/motion")
def api_node_motion(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    enabled = bool(payload.get("enabled", True))
    try:
        duration = int(payload.get("duration", 30))
    except Exception:
        raise HTTPException(400, "invalid duration")
    if not 10 <= duration <= 3600:
        raise HTTPException(400, "invalid duration")
    motion_manager.configure_node(node_id, enabled, duration)
    return {"ok": True}

@router.post("/api/node/{node_id}/ota/check")
def api_ota_check(node_id: str):
    _valid_node(node_id)
    get_bus().ota_check(node_id)
    return {"ok": True}


@router.get("/api/admin/status")
def api_admin_status():
    snapshot = status_monitor.snapshot()

    def _iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    nodes: Dict[str, Dict[str, Any]] = {}
    for _, _, node in registry.iter_nodes():
        node_id = node["id"]
        info = snapshot.get(node_id, {})
        nodes[node_id] = {
            "online": bool(info.get("online")),
            "last_ok": _iso(info.get("last_ok")),
            "last_seen": _iso(info.get("last_seen")),
            "status": info.get("status"),
        }
    now = datetime.now(timezone.utc).isoformat()
    return {"now": now, "timeout": status_monitor.timeout, "nodes": nodes}
