from __future__ import annotations

from typing import Any, Dict, List

from .shared import build_kitchen_presets

PRESETS: Dict[str, List[Dict[str, Any]]] = {
    "kitchen": build_kitchen_presets("sdsu-kitchen-node1"),
}

__all__ = ["PRESETS"]
