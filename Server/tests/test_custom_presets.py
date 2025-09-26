"""Tests for custom preset persistence helpers."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Iterator
from unittest import mock


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
                rate_limited: bool = True,
            ) -> None:
                self.white_calls.append(
                    (node_id, channel, effect, brightness, params, rate_limited)
                )

            def ws_set(
                self,
                node_id: str,
                strip: int,
                effect: str,
                brightness: int,
                params,
                rate_limited: bool = True,
            ) -> None:
                self.ws_calls.append(
                    (node_id, strip, effect, brightness, params, rate_limited)
                )

            def rgb_set(
                self,
                node_id: str,
                strip: int,
                effect: str,
                brightness: int,
                params,
                rate_limited: bool = True,
            ) -> None:
                self.rgb_calls.append(
                    (node_id, strip, effect, brightness, params, rate_limited)
                )

        snapshot = {
            "white": [
                {
                    "channel": 0,
                    "effect": "swell",
                    "brightness": "200",
                    "params": [],
                    "ms": "3000",
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
                        ("node-1", 0, "swell", 200, [], False),
                        ("node-1", 1, "solid", 0, [], False),
                    ],
                )
                self.assertEqual(
                    bus.ws_calls,
                    [
                        ("node-1", 0, "solid", 128, ["255", 0, 0], False),
                        ("node-1", 1, "rainbow", 0, None, False),
                    ],
                )
                self.assertEqual(
                    bus.rgb_calls,
                    [("node-1", 2, "color_swell", 45, [255, 64, 32], False)],
                )


class CustomPresetReorderTests(unittest.TestCase):
    def test_reorder_presets_updates_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "custom_presets.json"
            with _override_custom_preset_file(preset_path) as presets:
                presets.save_custom_preset(
                    "house-1",
                    "room-1",
                    {"id": "one", "name": "One", "actions": []},
                )
                presets.save_custom_preset(
                    "house-1",
                    "room-1",
                    {"id": "two", "name": "Two", "actions": []},
                )
                presets.save_custom_preset(
                    "house-1",
                    "room-1",
                    {"id": "three", "name": "Three", "actions": []},
                )

                initial = presets.list_custom_presets("house-1", "room-1")
                self.assertEqual([p["id"] for p in initial], ["one", "two", "three"])

                reordered = presets.reorder_custom_presets(
                    "house-1", "room-1", ["three", "one", "two"]
                )
                self.assertEqual([p["id"] for p in reordered], ["three", "one", "two"])

                persisted = presets.list_custom_presets("house-1", "room-1")
                self.assertEqual([p["id"] for p in persisted], ["three", "one", "two"])

    def test_reorder_presets_validates_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "custom_presets.json"
            with _override_custom_preset_file(preset_path) as presets:
                presets.save_custom_preset(
                    "house-1",
                    "room-1",
                    {"id": "one", "name": "One", "actions": []},
                )
                presets.save_custom_preset(
                    "house-1",
                    "room-1",
                    {"id": "two", "name": "Two", "actions": []},
                )

                with self.assertRaises(ValueError):
                    presets.reorder_custom_presets(
                        "house-1", "room-1", ["two", "two"]
                    )

                with self.assertRaises(ValueError):
                    presets.reorder_custom_presets("house-1", "room-1", ["two"])

                with self.assertRaises(ValueError):
                    presets.reorder_custom_presets(
                        "house-1", "room-1", ["missing", "one"]
                    )

                stored = presets.list_custom_presets("house-1", "room-1")
                self.assertEqual([p["id"] for p in stored], ["one", "two"])

    def test_reorder_presets_handles_empty_rooms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "custom_presets.json"
            with _override_custom_preset_file(preset_path) as presets:
                self.assertEqual(
                    presets.reorder_custom_presets("house-1", "room-1", []),
                    [],
                )

                with self.assertRaises(KeyError):
                    presets.reorder_custom_presets("house-1", "room-1", ["one"])


class RecordingClient:
    """Simple MQTT client stub that records published messages."""

    def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - exercised in tests
        self.published_messages = []

    def max_inflight_messages_set(self, value: int) -> None:  # pragma: no cover - noop
        self.max_inflight = value

    def max_queued_messages_set(self, value: int) -> None:  # pragma: no cover - noop
        self.max_queued = value

    def tls_set(self, *args, **kwargs) -> None:  # pragma: no cover - record TLS setup
        self.tls_config = {"args": args, "kwargs": kwargs}

    def tls_insecure_set(self, value: bool) -> None:  # pragma: no cover - record TLS flag
        self.tls_insecure = bool(value)

    def connect(self, host: str, port: int, keepalive: int) -> None:  # pragma: no cover - noop
        self.connection = (host, port, keepalive)

    def publish(
        self,
        topic: str,
        payload: str,
        qos: int = 0,
        retain: bool = False,
    ):
        self.published_messages.append((topic, payload, qos, retain))
        return type("_Info", (), {"rc": 0})()

    def loop_forever(self) -> None:  # pragma: no cover - return immediately for tests
        return

    def disconnect(self) -> None:  # pragma: no cover - noop
        pass


class PresetRateLimiterTests(unittest.TestCase):
    def test_apply_preset_publishes_all_actions_without_rate_limit(self) -> None:
        from app.mqtt_bus import MqttBus, topic_cmd
        from app.presets import apply_preset

        preset = {
            "actions": [
                {
                    "module": "ws",
                    "node": "node-99",
                    "strip": 0,
                    "effect": "solid",
                    "brightness": 128,
                    "params": [255, 0, 0],
                },
                {
                    "module": "ws",
                    "node": "node-99",
                    "strip": 1,
                    "effect": "rainbow",
                    "brightness": 64,
                    "params": [0, 255, 0],
                },
                {
                    "module": "white",
                    "node": "node-99",
                    "channel": 0,
                    "effect": "dim",
                    "brightness": 12,
                    "params": [0.5],
                },
            ]
        }

        with mock.patch("app.mqtt_bus.paho.Client", RecordingClient):
            bus = MqttBus(client_id="test-presets")

        try:
            apply_preset(bus, preset)

            published = [
                (topic, json.loads(payload), retain)
                for topic, payload, _qos, retain in bus.client.published_messages
            ]

            expected = [
                (
                    topic_cmd("node-99", "ws/set/0"),
                    {
                        "strip": 0,
                        "effect": "solid",
                        "brightness": 128,
                        "params": [255, 0, 0],
                    },
                    True,
                ),
                (
                    topic_cmd("node-99", "ws/set/1"),
                    {
                        "strip": 1,
                        "effect": "rainbow",
                        "brightness": 64,
                        "params": [0, 255, 0],
                    },
                    True,
                ),
                (
                    topic_cmd("node-99", "white/set/0"),
                    {
                        "channel": 0,
                        "effect": "dim",
                        "brightness": 12,
                        "params": [0.5],
                    },
                    True,
                ),
            ]

            self.assertEqual(published, expected)
            self.assertEqual(bus._pending_commands, {})
        finally:
            bus.shutdown()


if __name__ == "__main__":
    unittest.main()

