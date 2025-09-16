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
# controls.  Each entry maps an effect name to a list describing the
# parameters that should be collected for that effect.  The descriptors are
# interpreted in order and the resulting values are sent as a positional
# ``params`` array as described in ``docs/mqtt.md``.
#
# Supported descriptor ``type`` values:
#
# ``color``  – render an ``<input type="color">`` and append the selected
#              RGB triplet to the ``params`` array.
# ``slider`` – render an ``<input type="range">`` and append the selected
#              integer value.
# ``number`` – render an ``<input type="number">`` for floating‑point or
#              free‑form numeric input.
# ``toggle`` – render a checkbox and append 1 if checked, otherwise 0.

WS_PARAM_DEFS = {
    # Solid color – single RGB value
    "solid": [
        {"type": "color", "label": "Color"},
    ],

    # Rainbow effect – wavelength slider (number of pixels per cycle)
    "rainbow": [
        {"type": "slider", "label": "Wavelength", "min": 1, "max": 255, "value": 32},
    ],

    # Modern rainbow – fixed 80 pixel cycle
    "modern_rainbow": [],

    # Triple wave – three sets of color, wavelength and frequency
    "triple_wave": [
        {"type": "color", "label": "Wave 1 Color"},
        {"type": "number", "label": "Wave 1 Wavelength", "value": 30},
        {"type": "number", "label": "Wave 1 Frequency", "value": 0.20, "step": 0.01},
        {"type": "color", "label": "Wave 2 Color"},
        {"type": "number", "label": "Wave 2 Wavelength", "value": 45},
        {"type": "number", "label": "Wave 2 Frequency", "value": 0.15, "step": 0.01},
        {"type": "color", "label": "Wave 3 Color"},
        {"type": "number", "label": "Wave 3 Wavelength", "value": 60},
        {"type": "number", "label": "Wave 3 Frequency", "value": 0.10, "step": 0.01},
    ],

    # Flash – alternate between two RGB colors
    "flash": [
        {"type": "color", "label": "Color 1"},
        {"type": "color", "label": "Color 2"},
    ],

    # Fire – intensity slider plus two-colour gradient
    "fire": [
        {"type": "slider", "label": "Intensity", "min": 0, "max": 200, "value": 120},
        {"type": "color", "label": "Primary Color", "value": "#ff4000"},
        {"type": "color", "label": "Secondary Color", "value": "#ffd966"},
    ],

    # Spacewaves – three RGB colors for interfering waves
    "spacewaves": [
        {"type": "color", "label": "Wave 1 Color"},
        {"type": "color", "label": "Wave 2 Color"},
        {"type": "color", "label": "Wave 3 Color"},
    ],
}

WHITE_PARAM_DEFS = {
    "breathe": [
        {"type": "slider", "label": "Period (ms)", "min": 100, "max": 5000, "value": 1000},
    ],
    "swell": [
        {"type": "slider", "label": "Start Brightness", "min": 0, "max": 255, "value": 0},
        {"type": "slider", "label": "End Brightness", "min": 0, "max": 255, "value": 255},
        {"type": "slider", "label": "Time (ms)", "min": 0, "max": 10000, "value": 1000},
    ],
}

# ``solid`` is fundamental and must always exist for the web interface. Ensure
# a default parameter definition is present even if trimmed elsewhere.
WS_PARAM_DEFS.setdefault("solid", [{"type": "color", "label": "Color"}])

