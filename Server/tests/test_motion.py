from __future__ import annotations

import importlib
import sys
from typing import Any, Dict, List, Tuple

import pytest


class _NoopBus:
    """Minimal bus stub used when importing :mod:`app.motion`."""

    def pub(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def ws_set(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def rgb_set(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def white_set(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def sensor_motion_program(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def status_request(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def ota_check(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def all_off(self) -> None:  # pragma: no cover - noop
        pass


@pytest.fixture()
def motion_module(monkeypatch: pytest.MonkeyPatch):
    """Return a freshly imported ``app.motion`` module with a stubbed bus."""

    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())
    sys.modules.pop("app.motion", None)
    module = importlib.import_module("app.motion")
    try:
        yield module
    finally:
        sys.modules.pop("app.motion", None)


class _RecordingBus:
    def __init__(self) -> None:
        self.published: List[Tuple[str, Dict[str, Any], bool]] = []

    def pub(self, topic: str, payload: Dict[str, Any], retain: bool = False) -> None:
        self.published.append((topic, payload, retain))


def _build_manager(module):
    manager = module.MotionManager.__new__(module.MotionManager)
    manager.bus = _RecordingBus()
    manager.active = {}
    manager.config = {}
    manager.room_sensors = {}
    return manager


def test_turn_off_special_prefers_off_preset(monkeypatch: pytest.MonkeyPatch, motion_module):
    manager = _build_manager(motion_module)
    room_id = "kitchen"
    manager.active[room_id] = {"house_id": "del-sur", "preset_on": "swell-on"}

    preset_off = {"id": "swell-off", "actions": []}

    def fake_get_preset(house_id: str, rid: str, preset_id: str):
        if (house_id, rid, preset_id) == ("del-sur", room_id, "swell-off"):
            return preset_off
        return None

    applied: List[Dict[str, Any]] = []

    monkeypatch.setattr(motion_module, "get_preset", fake_get_preset)
    monkeypatch.setattr(motion_module, "apply_preset", lambda bus, preset: applied.append(preset))

    manager._turn_off_special(room_id)

    assert applied == [preset_off]
    assert manager.bus.published == []
    assert room_id not in manager.active


def test_turn_off_special_falls_back_to_hint(monkeypatch: pytest.MonkeyPatch, motion_module):
    manager = _build_manager(motion_module)
    room_id = "kitchen"
    manager.active[room_id] = {"house_id": "del-sur", "preset_on": "swell-on"}

    monkeypatch.setattr(motion_module, "get_preset", lambda *args, **kwargs: None)
    applied: List[Dict[str, Any]] = []
    monkeypatch.setattr(motion_module, "apply_preset", lambda bus, preset: applied.append(preset))

    manager._turn_off_special(room_id)

    expected_topic = motion_module.topic_cmd("kitchen", "motion/hint")
    assert applied == []
    assert manager.bus.published == [(expected_topic, {"hint": "swell-off"}, False)]
    assert room_id not in manager.active
