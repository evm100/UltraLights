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

# Effect parameter metadata used by the web UI to render effect-specific
# controls.  Each entry maps an effect name to a list of parameter
# descriptors.  Supported descriptor ``type`` values are ``color`` (render a
# color picker), ``slider`` (render an ``input[type=range]``) and ``toggle``
# (render a checkbox).  Sliders accept ``min``, ``max`` and ``value`` keys for
# range configuration.

WS_PARAM_DEFS = {
    "solid": [{"id": "color", "type": "color"}],
    "breathe": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 120}],
    "rainbow": [
        {"id": "period", "type": "slider", "min": 10, "max": 500, "value": 50},
        {"id": "reverse", "type": "toggle"},
    ],
    "twinkle": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 50}],
    "theater_chase": [
        {"id": "period", "type": "slider", "min": 10, "max": 500, "value": 50},
        {"id": "reverse", "type": "toggle"},
    ],
    "wipe": [
        {"id": "period", "type": "slider", "min": 10, "max": 500, "value": 50},
        {"id": "reverse", "type": "toggle"},
    ],
    "gradient_scroll": [
        {"id": "period", "type": "slider", "min": 10, "max": 500, "value": 50},
        {"id": "reverse", "type": "toggle"},
    ],
}

WHITE_PARAM_DEFS = {
    "graceful_on": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 200}],
    "graceful_off": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 200}],
    "motion_swell": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 200}],
    "day_night_curve": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 200}],
    "blink": [{"id": "period", "type": "slider", "min": 10, "max": 500, "value": 200}],
}

