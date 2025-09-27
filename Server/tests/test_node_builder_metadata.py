import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import node_builder


def test_white_channel_defaults_are_preserved():
    metadata = {
        "board": "esp32",
        "white": [
            {"index": 0, "enabled": True, "gpio": 21},
        ],
    }

    overrides = node_builder.metadata_to_overrides(metadata)

    assert overrides["CONFIG_UL_WHT0_PWM_HZ"][0] == 3000
    assert overrides["CONFIG_UL_WHT0_MIN"][0] == 0
    assert overrides["CONFIG_UL_WHT0_MAX"][0] == 255
    assert overrides["CONFIG_UL_WHT0_LEDC_CH"][0] == 0
    assert "CONFIG_UL_WHT1_MIN" not in overrides


def test_ws2812_channel_values_are_copied():
    metadata = {
        "board": "esp32",
        "ws2812": [
            {"index": 0, "enabled": True, "gpio": 5, "pixels": 120},
        ],
    }

    overrides = node_builder.metadata_to_overrides(metadata)

    assert overrides["CONFIG_UL_WS0_ENABLED"][0] == "y"
    assert overrides["CONFIG_UL_WS0_GPIO"][0] == 5
    assert overrides["CONFIG_UL_WS0_PIXELS"][0] == 120
