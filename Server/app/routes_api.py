from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException
from .mqtt_bus import MqttBus
from . import registry
from .effects import WS_EFFECTS, WHITE_EFFECTS

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
        if not (
            isinstance(params, list)
            and all(isinstance(p, (int, float)) for p in params)
        ):
            raise HTTPException(400, "invalid params")
        params = [float(p) for p in params]
    get_bus().ws_set(node_id, strip, effect, brightness, speed, params)
    return {"ok": True}

@router.post("/api/node/{node_id}/ws/power")
def api_ws_power(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    if not 0 <= strip < 4:
        raise HTTPException(400, "invalid strip")
    on = bool(payload.get("on", True))
    get_bus().ws_power(node_id, strip, on)
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

@router.post("/api/node/{node_id}/ota/check")
def api_ota_check(node_id: str):
    _valid_node(node_id)
    get_bus().ota_check(node_id)
    return {"ok": True}
