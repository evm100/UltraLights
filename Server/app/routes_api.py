from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException
from .mqtt_bus import MqttBus
from . import registry
from .effects import WS_EFFECTS, WHITE_EFFECTS, RGB_EFFECTS
from .presets import (
    get_preset,
    apply_preset,
    get_room_presets,
    save_custom_preset,
    delete_custom_preset,
    snapshot_to_actions,
)
from .motion import motion_manager
from .motion_schedule import motion_schedule
from .motion_prefs import motion_preferences
from .status_monitor import status_monitor
from .brightness_limits import brightness_limits
from .channel_names import channel_names


router = APIRouter()
BUS: Optional[MqttBus] = None

DEFAULT_SNAPSHOT_TIMEOUT = 3.0
MAX_CUSTOM_PRESET_NAME_LENGTH = 64
MAX_NODE_NAME_LENGTH = 120

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


def _normalize_limits(raw_limits: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    cleaned: Dict[str, Dict[str, int]] = {}
    for module, channels in raw_limits.items():
        if not isinstance(channels, dict):
            continue
        module_key = str(module)
        module_limits: Dict[str, int] = {}
        for channel, value in channels.items():
            channel_key = str(channel)
            try:
                limit_value = int(value)
            except (TypeError, ValueError):
                continue
            module_limits[channel_key] = max(0, min(255, limit_value))
        if module_limits:
            cleaned[module_key] = module_limits
    return cleaned


def _normalize_light_entries(
    items: Any,
    index_key: str,
    limits: Dict[str, int],
    *,
    include_color: bool = False,
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_index = item.get(index_key)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        entry: Dict[str, Any] = {}
        entry["enabled"] = bool(item.get("enabled"))
        effect = item.get("effect")
        if isinstance(effect, str):
            entry["effect"] = effect
        brightness = item.get("brightness")
        if isinstance(brightness, (int, float)):
            entry["brightness"] = max(0, min(255, int(brightness)))
        params = item.get("params")
        if isinstance(params, list):
            clean_params: List[Any] = []
            for value in params:
                if isinstance(value, (int, float)):
                    clean_params.append(float(value))
                elif isinstance(value, str):
                    clean_params.append(value)
            entry["params"] = clean_params
        if include_color:
            color = item.get("color")
            if isinstance(color, list):
                clean_color: List[int] = []
                for value in color[:3]:
                    try:
                        clean_color.append(max(0, min(255, int(value))))
                    except (TypeError, ValueError):
                        clean_color.append(0)
                if clean_color:
                    while len(clean_color) < 3:
                        clean_color.append(0)
                    entry["color"] = clean_color[:3]
        name_value: Optional[str] = None
        raw_name = item.get("name")
        if isinstance(raw_name, str):
            clean_name = raw_name.strip()
            if clean_name:
                name_value = clean_name
        if isinstance(names, dict):
            stored = names.get(str(index))
            if isinstance(stored, str):
                clean_stored = stored.strip()
                if clean_stored:
                    name_value = clean_stored
        if name_value:
            entry["name"] = name_value

        limit = limits.get(str(index))
        if limit is not None:
            entry["limit"] = int(limit)
        result[str(index)] = entry
    return result


def _format_node_state(
    node_id: str,
    node: Dict[str, Any],
    seq: int,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    uptime_value: Optional[int] = None
    uptime = payload.get("uptime_s")
    if isinstance(uptime, (int, float)):
        uptime_value = max(0, int(uptime))

    limits = _normalize_limits(brightness_limits.get_limits_for_node(node_id))
    name_map = channel_names.get_names_for_node(node_id)

    modules: Dict[str, Any] = {}
    available: set[str] = set()

    ws_state = _normalize_light_entries(
        payload.get("ws"),
        "strip",
        limits.get("ws", {}),
        include_color=True,
        names=name_map.get("ws", {}),
    )
    if ws_state:
        modules["ws"] = ws_state
        available.add("ws")

    rgb_state = _normalize_light_entries(
        payload.get("rgb"),
        "strip",
        limits.get("rgb", {}),
        include_color=True,
        names=name_map.get("rgb", {}),
    )
    if rgb_state:
        modules["rgb"] = rgb_state
        available.add("rgb")

    white_state = _normalize_light_entries(
        payload.get("white"),
        "channel",
        limits.get("white", {}),
        names=name_map.get("white", {}),
    )
    if white_state:
        modules["white"] = white_state
        available.add("white")

    ota_payload = payload.get("ota")
    if isinstance(ota_payload, dict) and ota_payload:
        modules["ota"] = ota_payload
        available.add("ota")

    registry_modules = node.get("modules")
    if isinstance(registry_modules, list):
        for mod in registry_modules:
            mod_key = str(mod)
            if mod_key == "sensor":
                continue
            if mod_key not in {"ws", "rgb", "white"}:
                available.add(mod_key)

    available_modules = sorted(mod for mod in available if mod != "sensor")
    if not available_modules and isinstance(registry_modules, list):
        available_modules = [
            str(mod)
            for mod in registry_modules
            if str(mod) != "sensor"
        ]

    return {
        "node": node_id,
        "seq": seq,
        "uptime_s": uptime_value,
        "modules": modules,
        "limits": limits,
        "available_modules": available_modules,
    }


def _module_index_sort_key(value: Any) -> tuple[int, Any]:
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _modules_to_snapshot(modules: Dict[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    if not isinstance(modules, dict):
        return snapshot

    for module_name, module_state in modules.items():
        if not isinstance(module_state, dict):
            continue

        if module_name == "white":
            entries: List[Dict[str, Any]] = []
            for key, entry in sorted(module_state.items(), key=lambda item: _module_index_sort_key(item[0])):
                if not isinstance(entry, dict):
                    continue
                try:
                    channel = int(str(key))
                except (TypeError, ValueError):
                    continue
                payload_entry = dict(entry)
                payload_entry["channel"] = channel
                entries.append(payload_entry)
            if entries:
                snapshot["white"] = entries
            continue

        if module_name in {"ws", "rgb"}:
            entries = []
            for key, entry in sorted(module_state.items(), key=lambda item: _module_index_sort_key(item[0])):
                if not isinstance(entry, dict):
                    continue
                try:
                    strip = int(str(key))
                except (TypeError, ValueError):
                    continue
                payload_entry = dict(entry)
                payload_entry["strip"] = strip
                entries.append(payload_entry)
            if entries:
                snapshot[module_name] = entries

    return snapshot


@router.delete("/api/node/{node_id}")
def api_remove_node(node_id: str):
    house, room, node = registry.find_node(node_id)
    if not node or not house or not room:
        raise HTTPException(404, "Unknown node id")
    try:
        removed = registry.remove_node(node_id)
    except KeyError:
        raise HTTPException(404, "Unknown node id")
    motion_manager.forget_node(node_id)
    status_monitor.forget(node_id)
    return {"ok": True, "node": removed}


@router.post("/api/node/{node_id}/name")
def api_set_node_name(node_id: str, payload: Dict[str, Any]):
    _valid_node(node_id)
    raw_name = payload.get("name")
    if raw_name is None:
        raise HTTPException(400, "missing name")
    if not isinstance(raw_name, str):
        raise HTTPException(400, "invalid name")
    clean_name = raw_name.strip()
    if not clean_name:
        raise HTTPException(400, "invalid name")
    if len(clean_name) > MAX_NODE_NAME_LENGTH:
        raise HTTPException(400, "name too long")
    try:
        node = registry.set_node_name(node_id, clean_name)
    except KeyError:
        raise HTTPException(404, "Unknown node id")
    motion_manager.update_node_name(node_id, clean_name)
    return {"ok": True, "node": node}


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


@router.post("/api/house/{house_id}/rooms/reorder")
def api_reorder_rooms(house_id: str, payload: Dict[str, Any]):
    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(400, "missing order")
    try:
        new_order = registry.reorder_rooms(house_id, order)
    except KeyError:
        raise HTTPException(404, "Unknown house")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "order": [str(room.get("id")) for room in new_order]}


@router.delete("/api/house/{house_id}/rooms/{room_id}")
def api_delete_room(house_id: str, room_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")

    seen: set[str] = set()
    node_ids: List[str] = []
    nodes = room.get("nodes")
    if isinstance(nodes, list):
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("id")
            if not isinstance(raw_id, str):
                continue
            node_id = raw_id.strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            node_ids.append(node_id)

    try:
        removed = registry.remove_room(house_id, room_id)
    except KeyError:
        raise HTTPException(404, "Unknown room")

    for node_id in node_ids:
        motion_manager.forget_node(node_id)
        status_monitor.forget(node_id)

    motion_manager.forget_room(house_id, room_id)
    motion_schedule.remove_room(house_id, room_id)

    return {"ok": True, "room": removed, "removed_nodes": node_ids}


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
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "node": node}


@router.get("/api/house/{house_id}/room/{room_id}/presets")
def api_list_room_presets(house_id: str, room_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")
    presets = get_room_presets(house_id, room_id)
    return {"presets": presets}


@router.post("/api/house/{house_id}/room/{room_id}/presets")
def api_create_room_preset(house_id: str, room_id: str, payload: Dict[str, Any]):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")

    raw_name = payload.get("name")
    if raw_name is None:
        raise HTTPException(400, "missing preset name")
    name = str(raw_name).strip()
    if not name:
        raise HTTPException(400, "missing preset name")
    if len(name) > MAX_CUSTOM_PRESET_NAME_LENGTH:
        raise HTTPException(400, "preset name too long")

    timeout_raw = payload.get("timeout", DEFAULT_SNAPSHOT_TIMEOUT)
    try:
        timeout_value = float(timeout_raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid timeout")
    if timeout_value != timeout_value or timeout_value <= 0:
        raise HTTPException(400, "timeout must be positive")

    node_entries: List[tuple[str, Dict[str, Any]]] = []
    seen_nodes: set[str] = set()
    room_nodes = room.get("nodes")
    if not isinstance(room_nodes, list):
        room_nodes = []
    for entry in room_nodes:
        if not isinstance(entry, dict):
            continue
        node_id = entry.get("id")
        if not isinstance(node_id, str):
            continue
        node_key = node_id.strip()
        if not node_key or node_key in seen_nodes:
            continue
        node = _valid_node(node_key)
        node_entries.append((node_key, node))
        seen_nodes.add(node_key)

    actions: List[Dict[str, Any]] = []
    bus = get_bus()
    for node_id, node in node_entries:
        info = status_monitor.status_for(node_id)
        since_seq_raw = info.get("seq") if isinstance(info, dict) else 0
        try:
            since_seq = int(since_seq_raw)
        except (TypeError, ValueError):
            since_seq = 0

        bus.status_request(node_id)
        seq, snapshot_payload = status_monitor.wait_for_snapshot(
            node_id, since_seq, timeout_value
        )
        if not isinstance(snapshot_payload, dict) or snapshot_payload.get("event") != "snapshot":
            raise HTTPException(504, f"Timed out waiting for status snapshot from {node_id}")

        formatted_state = _format_node_state(node_id, node, seq, snapshot_payload)
        modules = formatted_state.get("modules")
        module_snapshot = _modules_to_snapshot(modules if isinstance(modules, dict) else {})
        node_actions = snapshot_to_actions(node_id, module_snapshot)
        if node_actions:
            actions.extend(node_actions)

    existing_ids = {
        str(p.get("id"))
        for p in get_room_presets(house_id, room_id)
        if isinstance(p, dict) and p.get("id") is not None
    }
    base_id = registry.slugify(name)
    if not base_id:
        base_id = "preset"
    preset_id = base_id
    counter = 2
    while preset_id in existing_ids:
        preset_id = f"{base_id}-{counter}"
        counter += 1

    saved = save_custom_preset(
        house_id,
        room_id,
        {"id": preset_id, "name": name, "actions": actions, "source": "custom"},
    )

    presets = get_room_presets(house_id, room_id)
    return {"ok": True, "preset": saved, "presets": presets}


@router.delete("/api/house/{house_id}/room/{room_id}/presets")
def api_delete_room_preset(house_id: str, room_id: str, preset_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")

    preset_key = str(preset_id).strip()
    if not preset_key:
        raise HTTPException(400, "invalid preset id")

    if not delete_custom_preset(house_id, room_id, preset_key):
        raise HTTPException(404, "Unknown preset")

    presets = get_room_presets(house_id, room_id)
    return {"ok": True, "presets": presets}


@router.post("/api/house/{house_id}/room/{room_id}/preset/{preset_id}")
def api_apply_preset(house_id: str, room_id: str, preset_id: str):
    preset = get_preset(house_id, room_id, preset_id)
    if not preset:
        raise HTTPException(404, "Unknown preset")
    apply_preset(get_bus(), preset)
    return {"ok": True}


@router.get("/api/house/{house_id}/room/{room_id}/motion-immune")
def api_get_motion_immune(house_id: str, room_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")
    immune = sorted(motion_preferences.get_room_immune_nodes(house_id, room_id))
    return {"house_id": house_id, "room_id": room_id, "immune": immune}


@router.post("/api/house/{house_id}/room/{room_id}/motion-immune")
def api_set_motion_immune(house_id: str, room_id: str, payload: Dict[str, Any]):
    house, room = registry.find_room(house_id, room_id)
    if not house or not room:
        raise HTTPException(404, "Unknown room")

    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")

    raw_list = payload.get("immune", [])
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        raise HTTPException(400, "invalid immune list")

    available_nodes = {
        str(node.get("id"))
        for node in room.get("nodes", [])
        if node.get("id") is not None
    }

    clean_list: list[str] = []
    for value in raw_list:
        node_id = str(value).strip()
        if not node_id:
            continue
        if node_id not in available_nodes:
            raise HTTPException(400, f"unknown node: {node_id}")
        if node_id not in clean_list:
            clean_list.append(node_id)

    stored = motion_preferences.set_room_immune_nodes(house_id, room_id, clean_list)
    immune = sorted(stored)
    return {"ok": True, "immune": immune}


@router.post("/api/house/{house_id}/room/{room_id}/motion-schedule")
def api_set_motion_schedule(house_id: str, room_id: str, payload: Dict[str, Any]):
    if (house_id, room_id) not in motion_manager.room_sensors:
        raise HTTPException(404, "Motion schedule not supported for this room")
    schedule = payload.get("schedule")
    if not isinstance(schedule, list):
        raise HTTPException(400, "invalid schedule")
    if len(schedule) != motion_schedule.slot_count:
        raise HTTPException(400, "invalid schedule length")
    valid_presets = {p["id"] for p in get_room_presets(house_id, room_id)}
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


@router.post("/api/house/{house_id}/room/{room_id}/motion-schedule/color")
def api_set_motion_schedule_color(house_id: str, room_id: str, payload: Dict[str, Any]):
    if (house_id, room_id) not in motion_manager.room_sensors:
        raise HTTPException(404, "Motion schedule not supported for this room")
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")
    preset_id = payload.get("preset")
    if not isinstance(preset_id, str) or not preset_id.strip():
        raise HTTPException(400, "invalid preset")
    preset_key = preset_id.strip()
    color_value = payload.get("color")
    if not isinstance(color_value, str) or not color_value.strip():
        raise HTTPException(400, "invalid color")
    valid_presets = {
        str(p.get("id"))
        for p in get_room_presets(house_id, room_id)
        if p.get("id") is not None
    }
    if preset_key not in valid_presets:
        raise HTTPException(404, f"unknown preset: {preset_key}")
    try:
        stored = motion_schedule.set_preset_color(
            house_id, room_id, preset_key, color_value
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "preset": preset_key, "color": stored}

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


@router.post("/api/node/{node_id}/{module}/brightness-limit")
def api_set_brightness_limit(node_id: str, module: str, payload: Dict[str, Any]):
    node = _valid_node(node_id)
    module_key = str(module).lower()
    if module_key not in {"ws", "white", "rgb"}:
        raise HTTPException(404, "unsupported module")
    if module_key not in node.get("modules", []):
        raise HTTPException(404, "module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
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


@router.post("/api/node/{node_id}/{module}/channel-name")
def api_set_channel_name(node_id: str, module: str, payload: Dict[str, Any]):
    node = _valid_node(node_id)
    module_key = str(module).lower()
    if module_key not in {"ws", "white", "rgb"}:
        raise HTTPException(404, "unsupported module")
    if module_key not in node.get("modules", []):
        raise HTTPException(404, "module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
        raise HTTPException(400, "invalid channel")
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(400, "invalid name")
    stored = channel_names.set_name(node_id, module_key, channel, name)
    return {"ok": True, "name": stored}


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


@router.get("/api/node/{node_id}/state")
def api_node_state(node_id: str, timeout: float = 3.0):
    node = _valid_node(node_id)
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid timeout")
    if timeout_value != timeout_value or timeout_value <= 0:
        raise HTTPException(400, "timeout must be positive")

    info = status_monitor.status_for(node_id)
    since_seq_raw = info.get("seq") if isinstance(info, dict) else 0
    try:
        since_seq = int(since_seq_raw)
    except (TypeError, ValueError):
        since_seq = 0

    get_bus().status_request(node_id)
    seq, payload = status_monitor.wait_for_snapshot(node_id, since_seq, timeout_value)
    if not isinstance(payload, dict) or payload.get("event") != "snapshot":
        raise HTTPException(504, "Timed out waiting for status snapshot")

    return _format_node_state(node_id, node, seq, payload)


@router.get("/api/node/{node_id}/status")
def api_node_status(node_id: str):
    _valid_node(node_id)
    info = status_monitor.status_for(node_id)

    def _iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    signal_value: Optional[float] = None
    signal = info.get("signal_dbi")
    if isinstance(signal, (int, float)):
        signal_value = float(signal)

    return {
        "node": node_id,
        "online": bool(info.get("online")),
        "status": info.get("status"),
        "last_ok": _iso(info.get("last_ok")),
        "last_seen": _iso(info.get("last_seen")),
        "signal_dbi": signal_value,
        "timeout": status_monitor.timeout,
        "now": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/admin/status")
def api_admin_status(house_id: Optional[str] = None):
    snapshot = status_monitor.snapshot()

    def _iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    nodes: Dict[str, Dict[str, Any]] = {}
    if house_id is not None and not registry.find_house(house_id):
        raise HTTPException(404, "Unknown house id")
    for house, _, node in registry.iter_nodes():
        if house_id and house.get("id") != house_id:
            continue
        node_id = node["id"]
        info = snapshot.get(node_id, {})
        signal_value: Optional[float] = None
        signal = info.get("signal_dbi")
        if isinstance(signal, (int, float)):
            signal_value = float(signal)
        nodes[node_id] = {
            "online": bool(info.get("online")),
            "last_ok": _iso(info.get("last_ok")),
            "last_seen": _iso(info.get("last_seen")),
            "status": info.get("status"),
            "signal_dbi": signal_value,
        }
    now = datetime.now(timezone.utc).isoformat()
    return {"now": now, "timeout": status_monitor.timeout, "nodes": nodes}
