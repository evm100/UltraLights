import sys
from copy import deepcopy
from pathlib import Path
from typing import List

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


@pytest.fixture(autouse=True)
def _stub_mqtt(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())


def test_registry_remove_room(monkeypatch, tmp_path):
    from app import registry
    from app.config import settings

    test_registry = [
        {
            "id": "house",
            "name": "House",
            "rooms": [
                {"id": "room-a", "name": "Room A", "nodes": [{"id": "node-1"}]},
                {"id": "room-b", "name": "Room B", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    removed = registry.remove_room("house", "room-a")
    assert removed["id"] == "room-a"
    remaining = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in remaining] == ["room-b"]

    with pytest.raises(KeyError):
        registry.remove_room("house", "missing")


def test_api_delete_room_cleans_up(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings

    test_registry = [
        {
            "id": "house",
            "name": "House",
            "rooms": [
                {
                    "id": "room-a",
                    "name": "Room A",
                    "nodes": [
                        {"id": "node-1", "name": "Node 1"},
                        {"id": "node-2", "name": "Node 2"},
                    ],
                },
                {"id": "room-b", "name": "Room B", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    class Manager:
        def __init__(self) -> None:
            self.node_calls: List[str] = []
            self.room_calls: List[tuple[str, str]] = []

        def forget_node(self, node_id: str) -> None:
            self.node_calls.append(node_id)

        def forget_room(self, house_id: str, room_id: str) -> None:
            self.room_calls.append((house_id, room_id))

    class Status:
        def __init__(self) -> None:
            self.calls: List[str] = []

        def forget(self, node_id: str) -> None:
            self.calls.append(node_id)

    class Schedule:
        def __init__(self) -> None:
            self.calls: List[tuple[str, str]] = []

        def remove_room(self, house_id: str, room_id: str) -> None:
            self.calls.append((house_id, room_id))

    manager = Manager()
    status = Status()
    schedule = Schedule()

    monkeypatch.setattr(routes_api, "motion_manager", manager)
    monkeypatch.setattr(routes_api, "status_monitor", status)
    monkeypatch.setattr(routes_api, "motion_schedule", schedule)

    result = routes_api.api_delete_room("house", "room-a")

    assert result["ok"] is True
    assert result["room"]["id"] == "room-a"
    assert result["removed_nodes"] == ["node-1", "node-2"]

    remaining = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in remaining] == ["room-b"]

    assert manager.node_calls == ["node-1", "node-2"]
    assert manager.room_calls == [("house", "room-a")]
    assert status.calls == ["node-1", "node-2"]
    assert schedule.calls == [("house", "room-a")]


def test_api_delete_room_missing(monkeypatch):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", [
        {"id": "house", "rooms": []}
    ])

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_delete_room("house", "room-a")

    assert excinfo.value.status_code == 404
