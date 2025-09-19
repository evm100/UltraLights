"""Tests for custom preset persistence helpers."""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()

