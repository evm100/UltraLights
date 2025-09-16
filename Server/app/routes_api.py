from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException
from .mqtt_bus import MqttBus
from . import registry
from .effects import WS_EFFECTS, WHITE_EFFECTS, RGB_EFFECTS
from .presets import get_preset, apply_preset, get_room_presets
from .motion import motion_manager, SPECIAL_ROOM_PRESETS
from .motion_schedule import motion_schedule

router = APIRouter()
BUS: Optional[MqttBus] = None

def get_bus() -> MqttBus:
    global BUS
    if BUS is None:
        BUS = MqttBus()
    return BUS

def _valid_node(node_id: str) -> Dict[str, Any]:
    _, _, node = registry.find_node(node_id)
    if node:
        return node
    raise HTTPException(404, "Unknown node id")

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
    _valid_node(node_id)
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    if not 0 <= strip < 4:
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
    try:
        speed = float(payload.get("speed", 1.0))
    except Exception:
        raise HTTPException(400, "invalid speed")
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
    get_bus().ws_set(node_id, strip, effect, brightness, speed, params)
    return {"ok": True}

@router.post("/api/node/{node_id}/white/set")
def api_white_set(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
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
    _valid_node(node_id)
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    if not 0 <= strip < 4:
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
