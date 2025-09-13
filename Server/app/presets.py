from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mqtt_bus import MqttBus

# Presets are organized by house and room. Each preset contains a list of
# actions to perform when the preset is applied. Actions target a node and one
# of its modules (ws, white, etc.). This structure intentionally mirrors the
# existing command APIs so that presets can be expanded incrementally.
ROOM_PRESETS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    # "example-house": {
    #     "living-room": [
    #         {
    #             "id": "all-off",
    #             "name": "All Off",
    #             "actions": [
    #                 {
    #                     "node": "example-node",
    #                     "module": "ws_power",
    #                     "strip": 0,
    #                     "on": False,
    #                 }
    #             ],
    #         }
    #     ]
    # }
}


def get_room_presets(house_id: str, room_id: str) -> List[Dict[str, Any]]:
    """Return presets defined for ``house_id``/``room_id``."""
    return ROOM_PRESETS.get(house_id, {}).get(room_id, [])


def get_preset(house_id: str, room_id: str, preset_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a specific preset from ``house_id``/``room_id``."""
    for preset in get_room_presets(house_id, room_id):
        if preset.get("id") == preset_id:
            return preset
    return None


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
        elif module == "white":
            bus.white_set(
                node,
                int(action.get("channel", 0)),
                action.get("effect", ""),
                int(action.get("brightness", 0)),
                action.get("params"),
            )
        elif module == "ws_power":
            bus.ws_power(node, int(action.get("strip", 0)), bool(action.get("on", False)))
        elif module == "sensor_cooldown":
            bus.sensor_cooldown(node, int(action.get("seconds", 30)))
        else:
            # Unknown action type; ignore for now.
            continue
