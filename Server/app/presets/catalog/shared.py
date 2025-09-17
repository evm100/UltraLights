from __future__ import annotations

from typing import Any, Dict, List

from ..actions.white import white_swell_action, white_swell_actions


def build_kitchen_presets(node_id: str) -> List[Dict[str, Any]]:
    """Return the shared kitchen presets for ``node_id``."""

    return [
        {
            "id": "on",
            "name": "On 🟢",
            "actions": [
                white_swell_action(node_id, 0, 0, 255, 3000),
                white_swell_action(node_id, 1, 0, 255, 3000),
                white_swell_action("sala", 0, 0, 255, 3000),
            ],
        },
        {
            "id": "medium",
            "name": "Medium 🟡",
            "actions": [
                white_swell_action(node_id, 0, 0, 150, 3000),
                white_swell_action(node_id, 1, 0, 150, 3000),
                white_swell_action("sala", 0, 0, 150, 3000),
            ],
        },
        {
            "id": "off",
            "name": "Off 🔴",
            "actions": [
                white_swell_action(node_id, 0, 255, 0, 1000),
                white_swell_action(node_id, 1, 255, 0, 1000),
                white_swell_action("sala", 0, 255, 0, 1000),
            ],
        },
        {
            "id": "midnight-snack",
            "name": "Midnight Snack 🌠",
            "actions": [
                white_swell_action(node_id, 0, 0, 4, 2000),
                white_swell_action(node_id, 1, 0, 0, 2000),
            ],
        },

    ]


__all__ = ["build_kitchen_presets"]
