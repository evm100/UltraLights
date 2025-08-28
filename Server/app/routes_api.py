from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from .mqtt_bus import MqttBus
from .config import settings
from . import registry

router = APIRouter()
BUS: MqttBus | None = None

def get_bus() -> MqttBus:
    global BUS
    if BUS is None: BUS = MqttBus()
    return BUS

def _valid_node(node_id: str) -> Dict[str, Any]:
    """Return node dict for ``node_id`` or raise 404."""
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
    kind = str(payload.get("kind", "rgb"))
    modules = payload.get("modules")
    try:
        node = registry.add_node(house_id, room_id, name, kind, modules)
    except KeyError:
        raise HTTPException(404, "Unknown room")
    return {"ok": True, "node": node}

@router.post("/api/node/{node_id}/color")
def api_node_color(node_id: str, payload: Dict[str, int]):
    _valid_node(node_id)
    r = int(payload.get("r", 0)); g = int(payload.get("g", 0)); b = int(payload.get("b", 0))
    get_bus().send_color(node_id, r, g, b)
    return {"ok": True, "node": node_id, "published": {"r": r, "g": g, "b": b}}

@router.post("/api/node/{node_id}/effect")
def api_node_effect(node_id: str, payload: Dict[str, str]):
    _valid_node(node_id)
    name = str(payload.get("name", "static")).strip().lower()
    get_bus().send_effect(node_id, name)
    return {"ok": True, "node": node_id, "effect": name}

@router.post("/api/node/{node_id}/spacey")
def api_node_spacey(node_id: str, payload: Dict[str, list]):
    _valid_node(node_id)
    def clamp255(x): return max(0, min(255, int(x)))
    c1 = payload.get("c1", [128,0,255])
    c2 = payload.get("c2", [0,128,255])
    c3 = payload.get("c3", [255,64,0])
    a = ",".join(str(clamp255(x)) for x in c1)
    b = ",".join(str(clamp255(x)) for x in c2)
    c = ",".join(str(clamp255(x)) for x in c3)
    get_bus().send_spacey(node_id, a, b, c)
    return {"ok": True, "node": node_id}

@router.post("/api/node/{node_id}/brightness")
def api_node_brightness(node_id: str, payload: Dict[str, int]):
    _valid_node(node_id)
    level = max(0, min(255, int(payload.get("level", 0))))
    get_bus().send_brightness(node_id, level)
    return {"ok": True, "node": node_id, "brightness": level}


@router.post("/api/node/{node_id}/motion")
def api_node_motion(node_id: str, payload: Dict[str, bool]):
    _valid_node(node_id)
    enabled = bool(payload.get("enabled", False))
    get_bus().send_motion(node_id, enabled)
    return {"ok": True, "node": node_id, "enabled": enabled}

@router.post("/api/node/{node_id}/ota")
def api_node_ota(node_id: str, payload: Dict[str, str]):
    _valid_node(node_id)
    url = str(payload.get("url","")).strip()
    if not url: raise HTTPException(400, "missing url")
    get_bus().send_ota(node_id, url, retain=False)
    return {"ok": True, "node": node_id, "published": {"now": url}}
