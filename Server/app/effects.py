import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def _load_effects(rel: str) -> list[str]:
    path = ROOT / rel
    if not path.exists():
        return []
    text = path.read_text()
    return re.findall(r'\{"([a-zA-Z0-9_]+)"', text)

WS_EFFECTS = set(_load_effects("UltraNodeV5/components/ul_ws_engine/effects_ws/registry.c"))
WHITE_EFFECTS = set(_load_effects("UltraNodeV5/components/ul_white_engine/effects_white/registry.c"))
