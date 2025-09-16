from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..action_registry import register_action


def _reverse_white_swell(action: Dict[str, Any]) -> Dict[str, Any]:
    """Swap the start/end parameters for a swell action."""

    params = action.get("params")
    if isinstance(params, list) and len(params) >= 2:
        params[0], params[1] = params[1], params[0]

    if "start" in action and "end" in action:
        action["start"], action["end"] = action["end"], action["start"]

    return action


@register_action("white.swell", reverser=_reverse_white_swell)
def white_swell_action(
    node: str,
    channel: int,
    start: int,
    end: int,
    ms: int,
) -> Dict[str, Any]:
    """Return a single white-channel swell action."""

    return {
        "node": node,
        "module": "white",
        "channel": channel,
        "effect": "swell",
        "brightness": 255,
        "params": [start, end, ms],
    }


def white_swell_actions(
    nodes: Iterable[str],
    start: int,
    end: int,
    ms: int,
    *,
    channels: Optional[Iterable[int]] = None,
) -> List[Dict[str, Any]]:
    """Generate white-channel swell actions for ``nodes``.

    Each node receives one action per requested channel that starts at
    ``start`` brightness and swells to ``end`` over ``ms`` milliseconds.  If
    ``channels`` is ``None`` all four channels are targeted.
    """

    actions: List[Dict[str, Any]] = []
    channel_values = list(range(4)) if channels is None else list(channels)
    for node in nodes:
        for channel in channel_values:
            actions.append(white_swell_action(node, channel, start, end, ms))
    return actions


__all__ = [
    "white_swell_action",
    "white_swell_actions",
]
