from __future__ import annotations

from typing import Any, Dict, List

from ..actions.white import white_swell_action, white_swell_actions


def build_kitchen_presets(node_id: str) -> List[Dict[str, Any]]:
    """Return the shared kitchen presets for ``node_id``."""

    return [
        {
            "id": "swell-on",
            "name": "On",
            "actions": [
                white_swell_action(node_id, 0, 0, 255, 3000),
                white_swell_action(node_id, 1, 0, 255, 3000),
                white_swell_action(node_id, 2, 0, 255, 3000),
                white_swell_action("sala", 0, 0, 255, 3000),
            ],
        },
        {
            "id": "midnight-snack",
            "name": "Midnight Snack",
            "actions": [
                white_swell_action(node_id, 0, 0, 4, 2000),
                white_swell_action(node_id, 1, 0, 4, 2000),
                white_swell_action(node_id, 2, 0, 0, 5000),
            ],
        },
        {
            "id": "kitchens-closed",
            "name": "Kitchen's Closed",
            "actions": [
                white_swell_action(node_id, 2, 100, 255, 3000),
                white_swell_action(node_id, 1, 100, 0, 3000),
                white_swell_action(node_id, 0, 100, 0, 3000),
                white_swell_action("sala", 0, 100, 255, 3000),
            ],
        },
        {
            "id": "normal",
            "name": "Normal",
            "actions": [
                white_swell_action(node_id, 0, 0, 100, 3000),
                white_swell_action(node_id, 1, 0, 100, 3000),
                white_swell_action(node_id, 2, 0, 100, 3000),
                white_swell_action("sala", 0, 0, 100, 3000),
            ],
        },
        {
            "id": "normal-to-max",
            "name": "Normal to Max",
            "actions": white_swell_actions([node_id], 100, 255, 3000, channels=[0, 1, 2]),
        },
        {
            "id": "max-to-normal",
            "name": "Max to Normal",
            "actions": white_swell_actions([node_id], 100, 255, 3000, channels=[0, 1, 2]),
        },
    ]


__all__ = ["build_kitchen_presets"]
