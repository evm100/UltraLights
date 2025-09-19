"""Tests for custom preset persistence helpers."""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@contextlib.contextmanager
def _override_custom_preset_file(path: Path) -> Iterator[ModuleType]:
    """Temporarily point ``app.presets`` at ``path`` and reload the module."""

    original_env = os.environ.get("CUSTOM_PRESET_FILE")
    os.environ["CUSTOM_PRESET_FILE"] = str(path)

    for module_name in ["app.presets", "app.config"]:
        if module_name in sys.modules:
            del sys.modules[module_name]

    try:
        yield importlib.import_module("app.presets")
    finally:
        for module_name in ["app.presets", "app.config"]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        if original_env is None:
            os.environ.pop("CUSTOM_PRESET_FILE", None)
        else:
            os.environ["CUSTOM_PRESET_FILE"] = original_env


class CustomPresetRoundTripTests(unittest.TestCase):
    def test_metadata_free_action_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "custom_presets.json"
            with _override_custom_preset_file(preset_path) as presets:
                source_preset = {
                    "id": "movie-time",
                    "name": "Movie Time",
                    "actions": [
                        {
                            "module": "white",
                            "node": "family-room",
                            "channel": 0,
                            "effect": "dim",
                            "params": {"level": 12},
                        }
                    ],
                }

                saved = presets.save_custom_preset("house-1", "room-1", source_preset)

                # Metadata should remain optional and untouched.
                self.assertNotIn("_action_type", saved["actions"][0])

                # Mutating the original input should not affect persisted data.
                source_preset["actions"][0]["params"]["level"] = 99
                self.assertEqual(saved["actions"][0]["params"], {"level": 12})

                listed = presets.list_custom_presets("house-1", "room-1")
                self.assertEqual(len(listed), 1)
                round_tripped = listed[0]

                self.assertEqual(round_tripped["id"], "movie-time")
                self.assertEqual(round_tripped["name"], "Movie Time")
                self.assertEqual(round_tripped["actions"][0]["params"], {"level": 12})
                self.assertNotIn("_action_type", round_tripped["actions"][0])

    def test_snapshot_round_trip_preserves_actions(self) -> None:
        class RecordingBus:
            def __init__(self) -> None:
                self.white_calls = []
                self.ws_calls = []
                self.rgb_calls = []

            def white_set(
                self,
                node_id: str,
                channel: int,
                effect: str,
                brightness: int,
                params,
            ) -> None:
                self.white_calls.append((node_id, channel, effect, brightness, params))

            def ws_set(
                self,
                node_id: str,
                strip: int,
                effect: str,
                brightness: int,
                params,
            ) -> None:
                self.ws_calls.append((node_id, strip, effect, brightness, params))

            def rgb_set(
                self,
                node_id: str,
                strip: int,
                effect: str,
                brightness: int,
                params,
            ) -> None:
                self.rgb_calls.append((node_id, strip, effect, brightness, params))

        snapshot = {
            "white": [
                {
                    "channel": 0,
                    "effect": "swell",
                    "brightness": "200",
                    "params": [10, "200", 1500],
                    "ms": "1500",
                },
                {
                    "channel": "1",
                    "effect": "solid",
                    "brightness": None,
                    "params": [],
                },
            ],
            "ws": [
                {
                    "strip": "0",
                    "effect": "solid",
                    "brightness": "128",
                    "params": ("255", 0, 0),
                },
                {
                    "strip": 1,
                    "effect": "rainbow",
                    "brightness": "",
                    "params": None,
                    "extra": "value",
                },
            ],
            "rgb": [
                {
                    "strip": "2",
                    "effect": "color_swell",
                    "brightness": "45",
                    "params": ["255", "64", "32"],
                }
            ],
        }

        node_id = "node-1"
        expected_actions = []
        for module_name in ["white", "ws", "rgb"]:
            for entry in snapshot.get(module_name, []):
                action = deepcopy(entry)
                action["node"] = node_id
                action["module"] = module_name
                expected_actions.append(action)

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "custom_presets.json"
            with _override_custom_preset_file(preset_path) as presets:
                actions = presets.snapshot_to_actions(node_id, snapshot)
                self.assertEqual(actions, expected_actions)

                saved = presets.save_custom_preset(
                    "house-1",
                    "room-2",
                    {"id": "multi", "name": "Multi Module", "actions": actions},
                )
                self.assertEqual(saved["actions"], expected_actions)

                listed = presets.list_custom_presets("house-1", "room-2")
                self.assertEqual(len(listed), 1)
                round_tripped = listed[0]
                self.assertEqual(round_tripped["actions"], expected_actions)

                bus = RecordingBus()
                presets.apply_preset(bus, round_tripped)

                self.assertEqual(
                    bus.white_calls,
                    [
                        ("node-1", 0, "swell", 200, [10, "200", 1500]),
                        ("node-1", 1, "solid", 0, []),
                    ],
                )
                self.assertEqual(
                    bus.ws_calls,
                    [
                        ("node-1", 0, "solid", 128, ["255", 0, 0]),
                        ("node-1", 1, "rainbow", 0, None),
                    ],
                )
                self.assertEqual(
                    bus.rgb_calls,
                    [("node-1", 2, "color_swell", 45, [255, 64, 32])],
                )


if __name__ == "__main__":
    unittest.main()

