from __future__ import annotations

from copy import deepcopy
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

def _color_action(node: str, strip: int, r: int, g: int, b: int) -> Dict[str, Any]:
    return {
        "node": node,
        "module": "ws",
        "strip": strip,
        "effect": "solid",
        "brightness": 255,
        "params": [r,g,b],
    }
  
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
        "master": [],
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
            "name": "On",
            "actions": _white_swell_actions([node_id], 0, 255, 3000, channels=[0, 1, 2]),
        },
        {
            "id": "midnight-snack",
            "name": "Midnight Snack",
            "actions": [
                _white_swell_action(node_id, 0, 0, 4, 2000),
                _white_swell_action(node_id, 1, 0, 4, 2000),
                _white_swell_action(node_id, 2, 0, 0, 5000),
            ],
        },
        {
            "id": "kitchens-closed",
            "name": "Kitchen's Closed",
            "actions": [
                _white_swell_action(node_id, 2, 100, 255, 3000),
                _white_swell_action(node_id, 1, 100, 0, 3000),
                _white_swell_action(node_id, 0, 100, 0, 3000),
            ],
        },
        {
            "id": "normal",
            "name": "Normal",
            "actions": _white_swell_actions([node_id], 0, 100, 3000, channels=[0, 1, 2]),
        },
        {
            "id": "normal-to-max",
            "name": "Normal to Max",
            "actions": _white_swell_actions([node_id], 100, 255, 3000, channels=[0, 1, 2]),
        },
        {
            "id": "max-to-normal",
            "name": "Max to Normal",
            "actions": _white_swell_actions([node_id], 100, 255, 3000, channels=[0, 1, 2]),
        },
    ]

# Master closet presets for Del Sur
ROOM_PRESETS["del-sur"]["master"] = [
    {
        "id": "swell-on",
        "name": "On",
        "actions": _white_swell_actions(["master-closet"], 0, 255, 3000, channels=[0, 1]),
    },
    {
        "id": "half-bright",
        "name": "Half Bright",
        "actions": _white_swell_actions(["master-closet"], 0, 50, 2000, channels=[0, 1]),
    },
    {
        "id": "floor-on",
        "name": "Floor On",
        "actions": _white_swell_actions(["master-closet"], 0, 255, 3000, channels=[0]),
    },
    {
        "id": "nightlight",
        "name": "Night Light",
        "actions": _white_swell_actions(["master-closet"], 0, 4, 2000, channels=[0]),
        
    },
]

# Edgar presets
ROOM_PRESETS["del-sur"]["edgar"] = [
    {
        "id": "swell-on",
        "name": "On",
        "actions": _white_swell_actions(["amp-lights"], 0, 255, 3000, channels=[0, 1]),
    },
    {
        "id": "blue",
        "name": "Blue",
        "actions": [
            _color_action("amp-lights", 0, 20, 0, 55),
            _white_swell_action("amp-lights", 0, 0, 0, 3000),
            _white_swell_action("amp-lights", 1, 0, 0, 3000)
    ]
    },
    {
        "id": "guitar",
        "name": "Guitar",
        "actions": [
            _white_swell_action("amp-lights", 0, 0, 255, 3000),
            _white_swell_action("amp-lights", 1, 0, 6, 3000)
        ],
    },
    {
        "id": "guitar-no-amp",
        "name": "Guitar No Amp",
        "actions": [
            _white_swell_action("amp-lights", 0, 0, 255, 3000),
            _white_swell_action("amp-lights", 1, 0, 0, 3000)
        ],
    },
    {
        "id": "nightlight",
        "name": "Night Light",
        "actions": [
            _white_swell_action("amp-lights", 0, 0, 50, 2000),
            _white_swell_action("amp-lights", 1, 0, 0, 2000),
        ],
    },
    {
        "id": "all-off",
        "name": "Off",
        "actions": [
            _white_swell_action("amp-lights", 0, 0, 0, 2000),
            _white_swell_action("amp-lights", 1, 0, 0, 2000),
            _color_action("amp_lights", 0, 0, 0, 0),
        ],
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


def reverse_preset(preset: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copied ``preset`` with each action reversed."""

    reversed_preset = deepcopy(preset)
    reversed_actions: List[Dict[str, Any]] = []

    for action in preset.get("actions", []):
        new_action = deepcopy(action)

        if "start" in new_action and "end" in new_action:
            new_action["start"], new_action["end"] = new_action["end"], new_action["start"]

        module = new_action.get("module")
        effect = new_action.get("effect")
        params = new_action.get("params")

        if isinstance(params, list):
            params = list(params)
            swapped = False

            if module == "white" and effect == "swell" and len(params) >= 2:
                params[0], params[1] = params[1], params[0]
                swapped = True

            if swapped:
                new_action["params"] = params

        reversed_actions.append(new_action)

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
