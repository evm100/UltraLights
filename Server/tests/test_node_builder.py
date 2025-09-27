from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import node_builder


@pytest.mark.parametrize(
    "metadata, expected",
    [
        (
            {
                "board": "esp32c3",
                "ws2812": [
                    {"index": 0, "enabled": True, "gpio": 4, "pixels": 6},
                ],
                "white": [
                    {"index": 0, "enabled": True, "gpio": 1},
                    {"index": 1, "enabled": True, "gpio": 3},
                ],
            },
            {
                "CONFIG_UL_WS0_GPIO": (4, False),
                "CONFIG_UL_WS0_PIXELS": (6, False),
                "CONFIG_UL_WHT0_GPIO": (1, False),
                "CONFIG_UL_WHT0_LEDC_CH": (0, False),
                "CONFIG_UL_WHT1_GPIO": (3, False),
                "CONFIG_UL_WHT1_LEDC_CH": (1, False),
            },
        ),
        (
            {
                "board": "esp32",
                "ws2812": [
                    {"index": 1, "enabled": True, "gpio": 12, "pixels": 144},
                ],
                "white": [
                    {
                        "index": 2,
                        "enabled": False,
                        "gpio": 27,
                        "minimum": 5,
                        "maximum": 200,
                    },
                ],
            },
            {
                "CONFIG_UL_WS1_GPIO": (12, False),
                "CONFIG_UL_WS1_PIXELS": (144, False),
                "CONFIG_UL_WHT2_GPIO": (27, False),
                "CONFIG_UL_WHT2_MIN": (5, False),
                "CONFIG_UL_WHT2_MAX": (200, False),
            },
        ),
    ],
)
def test_metadata_to_overrides_preserves_light_channels(
    metadata: Dict[str, Any], expected: Dict[str, tuple[Any, bool]]
) -> None:
    normalized = node_builder.normalize_hardware_metadata(metadata)
    overrides = node_builder.metadata_to_overrides(normalized)

    for key, value in expected.items():
        assert overrides.get(key) == value


def test_normalize_hardware_metadata_sorts_ws_entries() -> None:
    raw = {
        "ws2812": [
            {"index": 1, "enabled": True, "gpio": 5},
            {"index": 0, "enabled": False, "gpio": 2, "pixels": 30},
        ]
    }

    normalized = node_builder.normalize_hardware_metadata(raw)
    assert normalized["ws2812"][0]["index"] == 0
    assert normalized["ws2812"][1]["index"] == 1
