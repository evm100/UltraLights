from __future__ import annotations

import importlib
import sys
import threading
import types
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

    def motion_status_request(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def ota_check(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - noop
        pass

    def all_off(self) -> None:  # pragma: no cover - noop
        pass


class _TestMotionPrefs:
    def __init__(self) -> None:
        self.rooms: Dict[Tuple[str, str], set[str]] = {}

    def get_room_immune_nodes(self, house_id: str, room_id: str) -> set[str]:
        return set(self.rooms.get((house_id, room_id), set()))

    def set_room_immune_nodes(
        self, house_id: str, room_id: str, nodes
    ) -> set[str]:
        clean: set[str] = set()
        for node in nodes:
            text = str(node).strip()
            if text:
                clean.add(text)
        key = (house_id, room_id)
        if clean:
            self.rooms[key] = set(clean)
        else:
            self.rooms.pop(key, None)
        return set(clean)

    def remove_node(self, node_id: str) -> None:
        text = str(node_id).strip()
        if not text:
            return
        to_delete: list[Tuple[str, str]] = []
        for key, nodes in self.rooms.items():
            if text in nodes:
                nodes.discard(text)
            if not nodes:
                to_delete.append(key)
        for key in to_delete:
            self.rooms.pop(key, None)


@pytest.fixture()
def motion_module(monkeypatch: pytest.MonkeyPatch):
    """Return a freshly imported ``app.motion`` module with a stubbed bus."""

    import app.mqtt_bus
    import app.motion_prefs

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())
    sys.modules.pop("app.motion", None)
    module = importlib.import_module("app.motion")
    test_prefs = _TestMotionPrefs()
    original_prefs = app.motion_prefs.motion_preferences
    app.motion_prefs.motion_preferences = test_prefs
    module.motion_preferences = test_prefs
    module.motion_manager.motion_preferences = test_prefs
    try:
        yield module
    finally:
        app.motion_prefs.motion_preferences = original_prefs
        module.motion_preferences = original_prefs
        if "motion_manager" in vars(module):
            module.motion_manager.motion_preferences = original_prefs
        sys.modules.pop("app.motion", None)


class _RecordingBus:
    def __init__(self) -> None:
        self.published: List[Tuple[str, Dict[str, Any], bool]] = []
        self.motion_status_requested: List[str] = []

    def pub(self, topic: str, payload: Dict[str, Any], retain: bool = False) -> None:
        self.published.append((topic, payload, retain))

    def motion_status_request(self, node_id: str) -> None:
        self.motion_status_requested.append(node_id)


def _build_manager(module):
    manager = module.MotionManager.__new__(module.MotionManager)
    manager.bus = _RecordingBus()
    manager.active = {}
    manager.config = {}
    manager.room_sensors = {}
    manager._status_request_lock = threading.Lock()
    manager._status_request_times = {}
    manager.motion_preferences = _TestMotionPrefs()
    return manager


def test_motion_preferences_round_trip(tmp_path):
    from app.motion_prefs import MotionPreferencesStore

    prefs_path = tmp_path / "motion_prefs.json"
    store = MotionPreferencesStore(prefs_path)

    assert store.get_room_immune_nodes("house", "room") == set()

    saved = store.set_room_immune_nodes(
        "house", "room", [" node-a ", "node-b", "node-a", ""]
    )
    assert saved == {"node-a", "node-b"}

    reloaded = MotionPreferencesStore(prefs_path)
    assert reloaded.get_room_immune_nodes("house", "room") == {"node-a", "node-b"}

    cleared = reloaded.set_room_immune_nodes("house", "room", [])
    assert cleared == set()
    assert reloaded.get_room_immune_nodes("house", "room") == set()

    reloaded.set_room_immune_nodes("house", "other", ["node-b"])
    reloaded.remove_node("node-b")
    assert reloaded.get_room_immune_nodes("house", "other") == set()

    final = MotionPreferencesStore(prefs_path)
    assert final.get_room_immune_nodes("house", "room") == set()
    assert final.get_room_immune_nodes("house", "other") == set()


def test_apply_motion_preset_filters_actions(monkeypatch: pytest.MonkeyPatch, motion_module):
    manager = _build_manager(motion_module)
    manager.motion_preferences.set_room_immune_nodes(
        "house", "room", ["immune-node"]
    )

    def preset_factory():
        return {
            "id": "motion-test",
            "actions": [
                {"module": "white", "node": "immune-node", "channel": 0, "effect": "off"},
                {"module": "white", "node": "active-node", "channel": 1, "effect": "on"},
                {"module": "white", "channel": 2, "effect": "dim"},
            ],
        }

    preset = preset_factory()
    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        motion_module,
        "apply_preset",
        lambda bus, payload: captured.append(payload),
    )

    applied = manager._apply_motion_preset("house", "room", preset)
    assert applied is True
    assert len(captured) == 1
    filtered_actions = captured[0]["actions"]
    assert len(filtered_actions) == 2
    nodes = [action.get("node") for action in filtered_actions if isinstance(action, dict)]
    assert "immune-node" not in nodes
    assert "active-node" in nodes
    assert len(preset["actions"]) == 3

    captured.clear()
    manager.motion_preferences.set_room_immune_nodes(
        "house", "room", ["immune-node", "active-node"]
    )
    immune_only = {
        "id": "immune-only",
        "actions": [
            {"module": "white", "node": "immune-node"},
            {"module": "white", "node": "active-node"},
        ],
    }
    applied = manager._apply_motion_preset("house", "room", immune_only)
    assert applied is False
    assert captured == []

def test_motion_event_applies_scheduled_preset(
    monkeypatch: pytest.MonkeyPatch, motion_module
) -> None:
    manager = _build_manager(motion_module)
    house = {"id": "del-sur", "name": "Del Sur"}
    room = {
        "id": "kitchen",
        "name": "Kitchen",
        "nodes": [{"id": "kitchen", "name": "Kitchen Node"}],
    }
    node = room["nodes"][0]

    manager.config["kitchen"] = {"enabled": True, "duration": 45}

    monkeypatch.setattr(
        motion_module.registry,
        "find_node",
        lambda node_id: (house, room, node)
        if node_id == "kitchen"
        else (None, None, None),
    )

    monkeypatch.setattr(
        motion_module.motion_schedule,
        "active_preset",
        lambda h, r, when=None: "on" if (h, r) == ("del-sur", "kitchen") else None,
    )

    requested: List[Tuple[str, str, str]] = []
    preset_payload = {"id": "on", "actions": [{"module": "white", "node": "kitchen"}]}

    def fake_get_preset(house_id: str, room_id: str, preset_id: str):
        requested.append((house_id, room_id, preset_id))
        if (house_id, room_id, preset_id) == ("del-sur", "kitchen", "on"):
            return preset_payload
        return None

    monkeypatch.setattr(motion_module, "get_preset", fake_get_preset)

    applied: List[Dict[str, Any]] = []
    monkeypatch.setattr(motion_module, "apply_preset", lambda bus, preset: applied.append(preset))

    class DummyTimer:
        def __init__(self, interval, func, args=None, kwargs=None):
            self.interval = interval
            self.func = func
            self.args = args or ()
            self.kwargs = kwargs or {}
            self.started = False

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:  # pragma: no cover - trivial stub
            pass

    monkeypatch.setattr(motion_module.threading, "Timer", DummyTimer)

    message = types.SimpleNamespace(
        topic="ul/kitchen/evt/pir/motion",
        payload=b'{"state": true}',
    )

    manager._on_message(None, None, message)

    assert requested
    assert requested[-1] == ("del-sur", "kitchen", "on")
    assert applied == [preset_payload]
    assert "kitchen" in manager.active
    entry = manager.active["kitchen"]
    assert entry["preset_on"] == "on"
    timer = entry["timers"].get("pir")
    assert isinstance(timer, DummyTimer)
    assert timer.started is True


def test_clear_sensor_reverses_active_preset(
    monkeypatch: pytest.MonkeyPatch, motion_module
) -> None:
    manager = _build_manager(motion_module)
    house_id = "house"
    room_id = "room"
    preset_id = "evening"
    original_preset = {
        "id": preset_id,
        "actions": [{"module": "white", "node": "node-1"}],
    }
    reversed_preset = {
        "id": f"{preset_id}-reverse",
        "actions": [{"module": "white", "node": "node-1"}],
    }

    manager.active[room_id] = {
        "house_id": house_id,
        "current": "pir",
        "timers": {"pir": object()},
        "preset_on": preset_id,
    }

    applied: List[Dict[str, Any]] = []
    seen: List[Dict[str, Any]] = []

    def fake_get_preset(house: str, room: str, preset: str):
        if (house, room, preset) == (house_id, room_id, preset_id):
            return original_preset
        return None

    def fake_reverse(preset: Dict[str, Any]) -> Dict[str, Any]:
        seen.append(preset)
        return reversed_preset

    monkeypatch.setattr(motion_module, "get_preset", fake_get_preset)
    monkeypatch.setattr(motion_module, "reverse_preset", fake_reverse)
    monkeypatch.setattr(
        motion_module,
        "apply_preset",
        lambda bus, payload: applied.append(payload),
    )

    manager._clear_sensor(room_id, "pir")

    assert room_id not in manager.active
    assert seen == [original_preset]
    assert applied == [reversed_preset]


def test_motion_event_skips_immune_nodes(monkeypatch: pytest.MonkeyPatch, motion_module):
    manager = _build_manager(motion_module)
    house = {"id": "house-1", "name": "House"}
    room = {
        "id": "room-1",
        "name": "Room",
        "nodes": [{"id": "node-1", "name": "Node"}],
    }
    node = room["nodes"][0]

    manager.config["node-1"] = {"enabled": True, "duration": 45}
    manager.motion_preferences.set_room_immune_nodes("house-1", "room-1", ["node-1"])

    monkeypatch.setattr(
        motion_module.registry,
        "find_node",
        lambda node_id: (house, room, node) if node_id == "node-1" else (None, None, None),
    )

    applied: List[Dict[str, Any]] = []

    monkeypatch.setattr(
        motion_module.motion_schedule,
        "active_preset",
        lambda h, r, when=None: "motion-far" if (h, r) == ("house-1", "room-1") else None,
    )

    def fake_get_preset(house_id: str, room_id: str, preset_id: str):
        if (house_id, room_id, preset_id) == ("house-1", "room-1", "motion-far"):
            return {
                "id": preset_id,
                "actions": [{"module": "white", "node": "node-1"}],
            }
        return None

    class DummyTimer:
        def __init__(self, interval, func, args=None, kwargs=None):
            self.interval = interval
            self.func = func
            self.args = args or ()
            self.kwargs = kwargs or {}
            self.started = False
            self.cancelled = False

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:  # pragma: no cover - simple stub
            self.cancelled = True

    monkeypatch.setattr(motion_module, "get_preset", fake_get_preset)
    monkeypatch.setattr(
        motion_module,
        "apply_preset",
        lambda bus, preset: applied.append(preset),
    )
    monkeypatch.setattr(motion_module.threading, "Timer", DummyTimer)

    message = types.SimpleNamespace(
        topic="ul/node-1/evt/pir/motion",
        payload=b'{"state": true}',
    )

    manager._on_message(None, None, message)

    assert applied == []
    assert room["id"] in manager.active
    entry = manager.active[room["id"]]
    assert entry["house_id"] == house["id"]
    assert entry["current"] == "pir"
    assert "pir" in entry["timers"]
    timer = entry["timers"]["pir"]
    assert isinstance(timer, DummyTimer)
    assert timer.started is True
    assert timer.interval == 45

    manager._clear_sensor(room["id"], "pir")
    assert applied == []


def test_motion_status_updates_config(monkeypatch: pytest.MonkeyPatch, motion_module) -> None:
    manager = _build_manager(motion_module)
    house = {"id": "house", "name": "House"}
    room = {"id": "room", "name": "Room", "nodes": [{"id": "node", "name": "Node"}]}
    node = room["nodes"][0]

    monkeypatch.setattr(
        motion_module.registry,
        "find_node",
        lambda node_id: (house, room, node) if node_id == "node" else (None, None, None),
    )

    message = types.SimpleNamespace(payload=b"{\"pir_enabled\": true}")
    manager._handle_motion_status_message("node", message)

    assert manager.config["node"]["pir_enabled"] is True
    room_key = (house["id"], room["id"])
    node_entry = manager.room_sensors[room_key]["nodes"]["node"]
    assert node_entry["config"]["pir_enabled"] is True
    assert manager.bus.motion_status_requested == []


def test_ensure_room_loaded_requests_status(monkeypatch: pytest.MonkeyPatch, motion_module) -> None:
    manager = _build_manager(motion_module)
    house = {"id": "house", "name": "House"}
    room = {"id": "room", "name": "Room", "nodes": [{"id": "node", "name": "Node"}]}
    node = room["nodes"][0]

    monkeypatch.setattr(
        motion_module.registry,
        "find_room",
        lambda h, r: (house, room) if (h, r) == ("house", "room") else (None, None),
    )
    monkeypatch.setattr(
        motion_module.registry,
        "find_node",
        lambda node_id: (house, room, node) if node_id == "node" else (None, None, None),
    )

    manager.ensure_room_loaded("house", "room")

    assert manager.bus.motion_status_requested == ["node"]
