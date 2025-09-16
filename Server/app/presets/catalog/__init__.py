from __future__ import annotations

from typing import Any, Dict, List

from .del_sur import PRESETS as DEL_SUR_PRESETS
from .sdsu import PRESETS as SDSU_PRESETS

ROOM_PRESETS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "del-sur": DEL_SUR_PRESETS,
    "sdsu": SDSU_PRESETS,
}

__all__ = ["ROOM_PRESETS"]
