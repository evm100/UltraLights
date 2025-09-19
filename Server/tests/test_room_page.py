from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from pathlib import Path
from typing import Iterator, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _NoopBus:
    def pub(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def ws_set(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def rgb_set(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def white_set(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def sensor_motion_program(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def status_request(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def motion_status_request(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def ota_check(self, *args, **kwargs):  # pragma: no cover - noop
        pass

    def all_off(self):  # pragma: no cover - noop
        pass


@pytest.fixture()
def app_modules(monkeypatch: pytest.MonkeyPatch) -> Iterator[Tuple[object, object]]:
    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())
    for module_name in ["app.motion", "app.routes_pages"]:
        if module_name in sys.modules:
            del sys.modules[module_name]
    motion = importlib.import_module("app.motion")
    routes_pages = importlib.import_module("app.routes_pages")
    try:
        yield motion, routes_pages
    finally:
        for module_name in ["app.routes_pages", "app.motion"]:
            if module_name in sys.modules:
                del sys.modules[module_name]


def test_room_page_motion_config_respects_room_sensors(app_modules) -> None:
    motion_module, routes_pages = app_modules
    from app.config import settings

    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_config = deepcopy(motion_module.motion_manager.config)
    original_room_sensors = deepcopy(motion_module.motion_manager.room_sensors)

    test_registry = [
        {
            "id": "test-house",
            "name": "Test House",
            "rooms": [
                {
                    "id": "with-motion",
                    "name": "Motion Room",
                    "nodes": [
                        {
                            "id": "sensor-node",
                            "name": "Sensor Node",
                            "modules": ["white"],
                        }
                    ],
                },
                {
                    "id": "no-motion",
                    "name": "Plain Room",
                    "nodes": [
                        {
                            "id": "plain-node",
                            "name": "Plain Node",
                            "modules": ["white"],
                        }
                    ],
                },
            ],
        }
    ]

    try:
        settings.DEVICE_REGISTRY = deepcopy(test_registry)
        manager = motion_module.motion_manager
        manager.config = {"sensor-node": {"enabled": True, "duration": 90, "pir_enabled": True}}
        manager.room_sensors = {
            ("test-house", "with-motion"): {
                "house_id": "test-house",
                "room_id": "with-motion",
                "room_name": "Motion Room",
                "nodes": {
                    "sensor-node": {
                        "node_id": "sensor-node",
                        "node_name": "Sensor Node",
                        "config": {"enabled": True, "duration": 90, "pir_enabled": True},
                        "sensors": {"pir": {"active": True}},
                    }
                },
            }
        }

        presets = []
        motion_config = routes_pages._build_motion_config("test-house", "with-motion", presets)
        assert motion_config is not None
        assert motion_config["sensors"][0]["node_id"] == "sensor-node"
        assert motion_config["sensors"][0]["duration"] == 90
        assert motion_config["sensors"][0]["pir_enabled"] is True

        motion_config_none = routes_pages._build_motion_config("test-house", "no-motion", presets)
        assert motion_config_none is None
    finally:
        settings.DEVICE_REGISTRY = original_registry
        motion_module.motion_manager.config = original_config
        motion_module.motion_manager.room_sensors = original_room_sensors
