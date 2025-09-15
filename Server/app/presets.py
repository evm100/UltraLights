from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mqtt_bus import MqttBus

def _white_swell_action(node: str, ch: int, start: int, end: int, ms: int) -> Dict[str, Any]:
    """Return a single white-channel swell action."""

    return {
        "node": node,
        "module": "white",
        "channel": ch,
        "effect": "swell",
        "brightness": 255,
        "params": [start, end, ms],
    }


def _white_swell_actions(nodes: List[str], start: int, end: int, ms: int,
                         channels: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Generate white-channel swell actions for ``nodes``.

    Each node receives one action per requested channel that starts at
    ``start`` brightness and swells to ``end`` over ``ms`` milliseconds.  If
    ``channels`` is ``None`` all four channels are targeted.
    """

    actions: List[Dict[str, Any]] = []
    if channels is None:
        channels = list(range(4))
    for node in nodes:
        for ch in channels:
            actions.append(_white_swell_action(node, ch, start, end, ms))
    return actions
  
# Presets are organized by house and room. Each preset contains a list of
# actions to perform when the preset is applied. Actions target a node and one
# of its modules (ws, white, etc.). This structure intentionally mirrors the
# existing command APIs so that presets can be expanded incrementally.
ROOM_PRESETS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    # Example preset: all white channels swell from 0 → 100 over 5s
    "del-sur": {
        "room-1": [
            {
                "id": "white-swell-100",
                "name": "White Swell 0→100",
                "actions": _white_swell_actions(
                    ["del-sur-room-1-node1", "node"], start=0, end=255, ms=5000
                ),
            }
        ],
        "kitchen": [],
    },
    "sdsu": {"kitchen": []},
}

# Kitchen presets for each house
for house_id, node_id in (
    ("del-sur", "kitchen"),
    ("sdsu", "sdsu-kitchen-node1"),
):
    ROOM_PRESETS[house_id]["kitchen"] = [
        {
            "id": "swell-on",
            "name": "Swell On",
            "actions": _white_swell_actions([node_id], 0, 255, 5000, channels=[0, 1, 2]),
        },
        {
            "id": "swell-off",
            "name": "Swell Off",
            "actions": _white_swell_actions([node_id], 255, 0, 5000, channels=[0, 1, 2]),
        },
        {
            "id": "midnight-snack",
            "name": "Midnight Snack",
            "actions": [
                _white_swell_action(node_id, 0, 0, 10, 2000),
                _white_swell_action(node_id, 1, 0, 50, 5000),
                _white_swell_action(node_id, 2, 0, 0, 5000),
            ],
        },
        {
            "id": "kitchens-closed",
            "name": "Kitchen's Closed",
            "actions": [
                _white_swell_action(node_id, 2, 150, 255, 5000),
                _white_swell_action(node_id, 1, 150, 0, 5000),
                _white_swell_action(node_id, 0, 150, 0, 5000),
            ],
        },
        {
            "id": "normal",
            "name": "Normal",
            "actions": _white_swell_actions([node_id], 0, 150, 5000, channels=[0, 1, 2]),
        },
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
