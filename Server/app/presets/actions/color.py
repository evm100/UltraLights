from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..action_registry import register_action

_DEFAULT_COLOR_OFF: List[float] = [0.0, 0.0, 0.0]


def _normalize_color(values: Sequence[float]) -> List[float]:
    return [float(component) for component in values]


def _reverse_ws_solid(action: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse a solid color action.

    The reverser swaps the current color with the color stored in
    ``_reverse_meta`` (if present).  When no metadata is supplied the action is
    reversed to "off" (0, 0, 0).
    """

    original_params = action.get("params")
    if isinstance(original_params, list):
        original_color = _normalize_color(original_params)
    else:
        original_color = list(_DEFAULT_COLOR_OFF)

    reverse_meta = action.get("_reverse_meta")
    target_color: Optional[List[float]] = None
    if isinstance(reverse_meta, dict):
        params = reverse_meta.get("params")
        if isinstance(params, (list, tuple)):
            target_color = _normalize_color(params)

    if target_color is None:
        target_color = list(_DEFAULT_COLOR_OFF)

    action["params"] = target_color
    action["_reverse_meta"] = {"params": original_color}
    return action


@register_action("ws.solid", reverser=_reverse_ws_solid)
def solid_color_action(
    node: str,
    strip: int,
    r: float,
    g: float,
    b: float,
    *,
    reverse_color: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Return an action that sets a WS strip to a solid color."""

    action: Dict[str, Any] = {
        "node": node,
        "module": "ws",
        "strip": strip,
        "effect": "solid",
        "brightness": 255,
        "params": [r, g, b],
    }

    if reverse_color is not None:
        action["_reverse_meta"] = {"params": _normalize_color(reverse_color)}

    return action


__all__ = ["solid_color_action"]
