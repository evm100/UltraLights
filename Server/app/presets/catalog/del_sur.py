from __future__ import annotations

from typing import Any, Dict, List

from ..actions import solid_color_action, white_swell_action, white_swell_actions
from .shared import build_kitchen_presets

PRESETS: Dict[str, List[Dict[str, Any]]] = {
    "room-1": [
        {
            "id": "white-swell-100",
            "name": "White Swell 0→100",
            "actions": white_swell_actions(
                ["del-sur-room-1-node1", "node"], start=0, end=255, ms=5000
            ),
        }
    ],
    "kitchen": build_kitchen_presets("kitchen"),
    "master": [
        {
            "id": "swell-on",
            "name": "On",
            "actions": white_swell_actions(["master-closet"], 0, 255, 3000, channels=[0, 1]),
        },
        {
            "id": "half-bright",
            "name": "Half Bright",
            "actions": white_swell_actions(["master-closet"], 0, 50, 2000, channels=[0, 1]),
        },
        {
            "id": "floor-on",
            "name": "Floor On",
            "actions": white_swell_actions(["master-closet"], 0, 255, 3000, channels=[0]),
        },
        {
            "id": "nightlight",
            "name": "Night Light",
            "actions": white_swell_actions(["master-closet"], 0, 4, 2000, channels=[0]),
        },
    ],
    "edgar": [
        {
            "id": "swell-on",
            "name": "On",
            "actions": white_swell_actions(["amp-lights"], 0, 255, 3000, channels=[0, 1]),
        },
        {
            "id": "blue",
            "name": "Blue",
            "actions": [
                solid_color_action("amp-lights", 0, 20, 0, 55),
                white_swell_action("amp-lights", 0, 0, 0, 3000),
                white_swell_action("amp-lights", 1, 0, 0, 3000),
            ],
        },
        {
            "id": "guitar",
            "name": "Guitar",
            "actions": [
                white_swell_action("amp-lights", 0, 0, 255, 3000),
                white_swell_action("amp-lights", 1, 0, 6, 3000),
            ],
        },
        {
            "id": "guitar-no-amp",
            "name": "Guitar No Amp",
            "actions": [
                white_swell_action("amp-lights", 0, 0, 255, 3000),
                white_swell_action("amp-lights", 1, 0, 0, 3000),
            ],
        },
        {
            "id": "nightlight",
            "name": "Night Light",
            "actions": [
                white_swell_action("amp-lights", 0, 0, 50, 2000),
                white_swell_action("amp-lights", 1, 0, 0, 2000),
            ],
        },
        {
            "id": "all-off",
            "name": "Off",
            "actions": [
                solid_color_action("amp-lights", 0, 0.0, 0.0, 0.0),
                white_swell_action("amp-lights", 0, 0, 0, 2000),
                white_swell_action("amp-lights", 1, 0, 0, 2000),
            ],
        },
    ],
}

__all__ = ["PRESETS"]
