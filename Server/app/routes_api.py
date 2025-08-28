from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from .mqtt_bus import MqttBus
from . import registry

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
    strip = int(payload.get("strip", 0))
    effect = payload.get("effect")
    color = payload.get("color")
    brightness = payload.get("brightness")
    get_bus().ws_set(node_id, strip, effect, color, brightness)
    return {"ok": True}

@router.post("/api/node/{node_id}/ws/power")
def api_ws_power(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    strip = int(payload.get("strip", 0))
    on = bool(payload.get("on", True))
    get_bus().ws_power(node_id, strip, on)
    return {"ok": True}

@router.post("/api/node/{node_id}/white/set")
def api_white_set(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    channel = int(payload.get("channel", 0))
    effect = payload.get("effect")
    brightness = payload.get("brightness")
    get_bus().white_set(node_id, channel, effect, brightness)
    return {"ok": True}

@router.post("/api/node/{node_id}/white/power")
def api_white_power(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    channel = int(payload.get("channel", 0))
    on = bool(payload.get("on", True))
    get_bus().white_power(node_id, channel, on)
    return {"ok": True}

@router.post("/api/node/{node_id}/sensor/cooldown")
def api_sensor_cooldown(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    seconds = int(payload.get("seconds", 60))
    get_bus().sensor_cooldown(node_id, seconds)
    return {"ok": True, "seconds": seconds}

@router.post("/api/node/{node_id}/ota/check")
def api_ota_check(node_id: str):
    _valid_node(node_id)
    get_bus().ota_check(node_id)
    return {"ok": True}
