from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import settings
from ..mqtt_bus import MqttBus
from .action_registry import (
    ActionDict,
    action_registry,
    register_action,
    reverse_action,
    with_action_type,
)
from .actions import rgb_swell_action, solid_color_action, white_swell_action, ws_swell_action
from .catalog import ROOM_PRESETS
from .custom_store import CustomPresetStore


_custom_presets = CustomPresetStore(settings.CUSTOM_PRESET_FILE)

__all__ = [
    "ROOM_PRESETS",
    "action_registry",
    "apply_preset",
    "delete_custom_preset",
    "get_preset",
    "get_room_presets",
    "list_custom_presets",
    "register_action",
    "reverse_preset",
    "save_custom_preset",
    "snapshot_to_actions",
    "with_action_type",
]


def get_room_presets(house_id: str, room_id: str) -> List[Dict[str, Any]]:
    """Return presets defined for ``house_id``/``room_id``."""

    house_key = str(house_id)
    room_key = str(room_id)

    presets: List[Dict[str, Any]] = []
    catalog_presets = ROOM_PRESETS.get(house_key, {}).get(room_key, [])
    if catalog_presets:
        presets.extend(deepcopy(catalog_presets))

    custom_presets = _custom_presets.list_presets(house_key, room_key)
    if custom_presets:
        presets.extend(custom_presets)

    return presets


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


def reverse_preset(preset: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copied ``preset`` with each action reversed."""

    reversed_preset = deepcopy(preset)
    reversed_actions: List[Dict[str, Any]] = []

    for action in preset.get("actions", []):
        try:
            reversed_action = reverse_action(action)
        except (KeyError, TypeError, ValueError):
            reversed_action = deepcopy(action)
        reversed_actions.append(reversed_action)

    reversed_preset["actions"] = reversed_actions
    return reversed_preset


def apply_preset(bus: MqttBus, preset: Dict[str, Any]) -> None:
    """Apply ``preset`` by sending commands through ``bus``."""

    for action in preset.get("actions", []):
        node = action.get("node")
        module = action.get("module")
        if module == "ws":
            bus.ws_set(
                node,
                int(action.get("strip", 0)),
                action.get("effect", ""),
                int(action.get("brightness", 0)),
                action.get("params"),
            )
        elif module == "rgb":
            params = action.get("params")
            clean_params: Optional[List[int]]
            if params is None:
                clean_params = None
            elif isinstance(params, list):
                clean_params = [int(p) for p in params]
            else:
                clean_params = None
            bus.rgb_set(
                node,
                int(action.get("strip", 0)),
                action.get("effect", ""),
                int(action.get("brightness", 0)),
                clean_params,
            )
        elif module == "white":
            bus.white_set(
                node,
                int(action.get("channel", 0)),
                action.get("effect", ""),
                int(action.get("brightness", 0)),
                action.get("params"),
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


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _extract_int(
    *candidates: Any,
    default: Optional[int] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> Optional[int]:
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            result = int(float(candidate))
        except (TypeError, ValueError):
            continue
        if min_value is not None and result < min_value:
            result = min_value
        if max_value is not None and result > max_value:
            result = max_value
        return result
    return default


def _extract_index(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    if result < 0:
        return None
    return result


def _extract_color(entry: Dict[str, Any], params: Sequence[Any]) -> Optional[Tuple[float, float, float]]:
    sources: List[Sequence[Any]] = []
    color_value = entry.get("color")
    if isinstance(color_value, (list, tuple)):
        sources.append(color_value)
    if isinstance(params, (list, tuple)) and params:
        sources.append(params)

    for source in sources:
        if len(source) < 3:
            continue
        try:
            r = float(source[0])
            g = float(source[1])
            b = float(source[2])
        except (TypeError, ValueError):
            continue
        return (r, g, b)
    return None


def _white_entry_to_action(node_id: str, channel: int, entry: Dict[str, Any]) -> Optional[ActionDict]:
    effect = entry.get("effect")
    if not isinstance(effect, str):
        return None

    effect_key = effect.strip().lower()
    if effect_key != "swell":
        return None

    params = _as_list(entry.get("params"))
    start = _extract_int(
        entry.get("start"),
        params[0] if len(params) > 0 else None,
        default=0,
        min_value=0,
        max_value=255,
    )
    if start is None:
        start = 0

    end = _extract_int(
        entry.get("end"),
        params[1] if len(params) > 1 else None,
        default=start,
        min_value=0,
        max_value=255,
    )
    if end is None:
        end = start

    ms = _extract_int(
        entry.get("ms"),
        params[2] if len(params) > 2 else None,
        default=0,
        min_value=0,
    )
    if ms is None:
        ms = 0

    action = white_swell_action(str(node_id), channel, start, end, ms)
    brightness = _extract_int(entry.get("brightness"), default=None, min_value=0, max_value=255)
    if brightness is not None:
        action["brightness"] = brightness
    action["start"] = start
    action["end"] = end
    return action


def _color_entry_to_action(
    node_id: str,
    module: str,
    index: int,
    entry: Dict[str, Any],
) -> Optional[ActionDict]:
    effect = entry.get("effect")
    if not isinstance(effect, str):
        return None

    effect_key = effect.strip().lower()
    params = _as_list(entry.get("params"))
    brightness = _extract_int(entry.get("brightness"), default=None, min_value=0, max_value=255)

    if effect_key == "solid":
        if module != "ws":
            return None
        color = _extract_color(entry, params)
        if color is None:
            return None
        action = solid_color_action(str(node_id), index, color[0], color[1], color[2])
        if brightness is not None:
            action["brightness"] = brightness
        return action

    if effect_key == "color_swell":
        color = _extract_color(entry, params)
        if color is None:
            return None

        start = _extract_int(
            entry.get("start"),
            params[3] if len(params) > 3 else None,
            default=0,
            min_value=0,
            max_value=255,
        )
        if start is None:
            start = 0

        end = _extract_int(
            entry.get("end"),
            params[4] if len(params) > 4 else None,
            default=start,
            min_value=0,
            max_value=255,
        )
        if end is None:
            end = start

        ms = _extract_int(
            entry.get("ms"),
            params[5] if len(params) > 5 else None,
            default=0,
            min_value=0,
        )
        if ms is None:
            ms = 0

        if module == "ws":
            builder = ws_swell_action
        elif module == "rgb":
            builder = rgb_swell_action
        else:
            return None

        action = builder(str(node_id), index, color[0], color[1], color[2], start, end, ms)
        if brightness is not None:
            action["brightness"] = brightness
        action["start"] = start
        action["end"] = end
        return action

    return None


def snapshot_to_actions(node_id: str, snapshot: Dict[str, Any]) -> List[ActionDict]:
    """Translate ``snapshot`` information for ``node_id`` into preset actions."""

    if not isinstance(snapshot, dict):
        return []

    node_key = str(node_id)
    actions: List[ActionDict] = []

    white_entries = snapshot.get("white")
    if isinstance(white_entries, list):
        for entry in white_entries:
            if not isinstance(entry, dict):
                continue
            channel = _extract_index(entry.get("channel"))
            if channel is None:
                continue
            action = _white_entry_to_action(node_key, channel, entry)
            if action is not None:
                actions.append(action)

    ws_entries = snapshot.get("ws")
    if isinstance(ws_entries, list):
        for entry in ws_entries:
            if not isinstance(entry, dict):
                continue
            strip = _extract_index(entry.get("strip"))
            if strip is None:
                continue
            action = _color_entry_to_action(node_key, "ws", strip, entry)
            if action is not None:
                actions.append(action)

    rgb_entries = snapshot.get("rgb")
    if isinstance(rgb_entries, list):
        for entry in rgb_entries:
            if not isinstance(entry, dict):
                continue
            strip = _extract_index(entry.get("strip"))
            if strip is None:
                continue
            action = _color_entry_to_action(node_key, "rgb", strip, entry)
            if action is not None:
                actions.append(action)

    return actions
