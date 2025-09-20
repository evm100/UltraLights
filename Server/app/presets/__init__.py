from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..config import settings
from ..mqtt_bus import MqttBus
from .action_registry import ActionDict, action_registry, register_action
from .custom_store import CustomPresetStore


_custom_presets = CustomPresetStore(settings.CUSTOM_PRESET_FILE)

__all__ = [
    "action_registry",
    "apply_preset",
    "delete_custom_preset",
    "get_preset",
    "get_room_presets",
    "list_custom_presets",
    "register_action",
    "save_custom_preset",
    "snapshot_to_actions",
]


def get_room_presets(house_id: str, room_id: str) -> List[Dict[str, Any]]:
    """Return presets defined for ``house_id``/``room_id``."""

    return _custom_presets.list_presets(str(house_id), str(room_id))


def list_custom_presets(house_id: str, room_id: str) -> List[Dict[str, Any]]:
    """Return custom presets stored for ``house_id``/``room_id``."""

    return _custom_presets.list_presets(str(house_id), str(room_id))


def save_custom_preset(house_id: str, room_id: str, preset: Dict[str, Any]) -> Dict[str, Any]:
    """Persist ``preset`` for ``house_id``/``room_id``.

    The preset definition is normalized so identifiers are sanitized and each
    action is deep-copied before persisting.  Metadata such as
    ``_action_type`` remains optional so user-supplied actions pass through as
    provided.
    """

    normalized = _normalize_custom_preset(preset)
    return _custom_presets.save_preset(str(house_id), str(room_id), normalized)


def delete_custom_preset(house_id: str, room_id: str, preset_id: str) -> bool:
    """Remove the custom preset ``preset_id`` from ``house_id``/``room_id``."""

    return _custom_presets.delete_preset(str(house_id), str(room_id), str(preset_id))


def get_preset(house_id: str, room_id: str, preset_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a specific preset from ``house_id``/``room_id``."""

    for preset in get_room_presets(house_id, room_id):
        if preset.get("id") == preset_id:
            return preset
    return None


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return default
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return int(text, 10)
        except ValueError:
            try:
                candidate = float(text)
            except ValueError:
                return default
            if math.isnan(candidate):
                return default
            return int(candidate)
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        candidate_float = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(candidate_float):
        return default
    return int(candidate_float)


def _normalize_sequence(value: Any) -> Optional[List[Any]]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return None


def _normalize_rgb_params(value: Any) -> Optional[List[int]]:
    params = _normalize_sequence(value)
    if params is None:
        return None
    return [_coerce_int(item, default=0) for item in params]


def apply_preset(bus: MqttBus, preset: Dict[str, Any]) -> None:
    """Apply ``preset`` by sending commands through ``bus``."""

    actions = preset.get("actions", [])
    if not isinstance(actions, list):
        return

    for raw_action in actions:
        if not isinstance(raw_action, dict):
            continue

        module_raw = raw_action.get("module")
        node_raw = raw_action.get("node")
        module = str(module_raw).strip().lower() if module_raw is not None else ""
        node = str(node_raw).strip() if node_raw is not None else ""
        if not module or not node:
            continue

        effect_raw = raw_action.get("effect")
        effect = str(effect_raw) if effect_raw is not None else ""
        brightness = _coerce_int(raw_action.get("brightness"), default=0)

        if module == "ws":
            strip = _coerce_int(raw_action.get("strip"), default=0)
            params = _normalize_sequence(raw_action.get("params"))
            bus.ws_set(node, strip, effect, brightness, params, rate_limited=False)
        elif module == "rgb":
            strip = _coerce_int(raw_action.get("strip"), default=0)
            params = _normalize_rgb_params(raw_action.get("params"))
            bus.rgb_set(node, strip, effect, brightness, params, rate_limited=False)
        elif module == "white":
            channel = _coerce_int(raw_action.get("channel"), default=0)
            params = _normalize_sequence(raw_action.get("params"))
            bus.white_set(
                node,
                channel,
                effect,
                brightness,
                params,
                rate_limited=False,
            )
        else:
            # Unknown action type; ignore for now.
            continue


def _normalize_custom_preset(preset: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(preset, dict):
        raise TypeError("preset must be a dictionary")

    preset_id = preset.get("id")
    if preset_id is None:
        raise ValueError("preset is missing an 'id'")

    preset_id_str = str(preset_id)
    if not preset_id_str:
        raise ValueError("preset id cannot be empty")

    raw_name = preset.get("name")
    if raw_name is None:
        name = preset_id_str
    else:
        name = str(raw_name).strip()
        if not name:
            name = preset_id_str

    actions = preset.get("actions")
    if actions is None:
        raise ValueError("preset must include an 'actions' list")
    if not isinstance(actions, list):
        raise TypeError("preset actions must be provided as a list")

    clean_actions: List[ActionDict] = []
    for action in actions:
        if not isinstance(action, dict):
            raise TypeError("preset actions must be dictionaries")
        clean_actions.append(deepcopy(action))

    clean: Dict[str, Any] = {
        key: deepcopy(value)
        for key, value in preset.items()
        if key not in {"actions", "id", "name"}
    }
    clean["id"] = preset_id_str
    clean["name"] = name
    clean["actions"] = clean_actions
    return clean


def snapshot_to_actions(node_id: str, snapshot: Dict[str, Any]) -> List[ActionDict]:
    """Translate ``snapshot`` information for ``node_id`` into preset actions."""

    if not isinstance(snapshot, dict):
        return []

    node_key = str(node_id).strip()
    if not node_key:
        return []

    actions: List[ActionDict] = []

    def _collect(module: str, index_field: str) -> None:
        entries = snapshot.get(module)
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get(index_field) is None:
                continue
            action = deepcopy(entry)
            action["node"] = node_key
            action["module"] = module
            actions.append(action)

    _collect("white", "channel")
    _collect("ws", "strip")
    _collect("rgb", "strip")

    return actions
