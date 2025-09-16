from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..mqtt_bus import MqttBus
from .action_registry import (
    action_registry,
    register_action,
    reverse_action,
    with_action_type,
)
from .catalog import ROOM_PRESETS

__all__ = [
    "ROOM_PRESETS",
    "action_registry",
    "apply_preset",
    "get_preset",
    "get_room_presets",
    "register_action",
    "reverse_preset",
    "with_action_type",
]


def get_room_presets(house_id: str, room_id: str) -> List[Dict[str, Any]]:
    """Return presets defined for ``house_id``/``room_id``."""

    return ROOM_PRESETS.get(house_id, {}).get(room_id, [])


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
                float(action.get("speed", 1.0)),
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
        elif module == "sensor_cooldown":
            bus.sensor_cooldown(node, int(action.get("seconds", 30)))
        else:
            # Unknown action type; ignore for now.
            continue
