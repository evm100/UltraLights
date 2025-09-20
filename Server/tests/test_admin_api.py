import json
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


def test_registry_reorder_rooms(monkeypatch, tmp_path):
    from app import registry
    from app.config import settings

    test_registry = [
        {
            "id": "house",
            "name": "House",
            "rooms": [
                {"id": "room-a", "name": "Room A", "nodes": []},
                {"id": "room-b", "name": "Room B", "nodes": []},
                {"id": "room-c", "name": "Room C", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    result = registry.reorder_rooms("house", ["room-c", "room-a", "room-b"])
    assert [room["id"] for room in result] == ["room-c", "room-a", "room-b"]
    stored = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in stored] == ["room-c", "room-a", "room-b"]

    written = json.loads(settings.REGISTRY_FILE.read_text())
    assert [room["id"] for room in written[0]["rooms"]] == ["room-c", "room-a", "room-b"]

    with pytest.raises(ValueError):
        registry.reorder_rooms("house", ["room-a", "room-a"])

    with pytest.raises(ValueError):
        registry.reorder_rooms("house", ["room-a", "room-b"])


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


def test_api_add_node_house_prefixed_id(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings

    test_registry = [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {"id": "kitchen", "name": "Kitchen", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    result = routes_api.api_add_node("del-sur", "kitchen", {"name": "Kitchen Node"})

    assert result["ok"] is True
    assert result["node"]["id"] == "del-sur-kitchen-node"
    stored_nodes = settings.DEVICE_REGISTRY[0]["rooms"][0]["nodes"]
    assert stored_nodes[0]["id"] == "del-sur-kitchen-node"


def test_api_add_node_duplicate_name(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    test_registry = [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "nodes": [
                        {"id": "del-sur-kitchen-node", "name": "Kitchen Node", "kind": "ultranode"}
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_add_node("del-sur", "kitchen", {"name": "Kitchen Node"})

    assert excinfo.value.status_code == 400
    assert "already exists" in str(excinfo.value.detail)


def test_api_add_node_rejects_long_id(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    test_registry = [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {"id": "kitchen", "name": "Kitchen", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_add_node("del-sur", "kitchen", {"name": "x" * 50})

    assert excinfo.value.status_code == 400
    assert str(excinfo.value.detail) == "node id too long (max 31 characters)"


def test_api_set_node_name(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    test_registry = [
        {
            "id": "house",
            "rooms": [
                {
                    "id": "room-a",
                    "nodes": [
                        {"id": "node-1", "name": "Original Name", "modules": ["ws"]},
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    class MotionStub:
        def __init__(self) -> None:
            self.updates: List[tuple[str, str]] = []

        def update_node_name(self, node_id: str, name: str) -> None:
            self.updates.append((node_id, name))

    motion_stub = MotionStub()
    monkeypatch.setattr(routes_api, "motion_manager", motion_stub)

    result = routes_api.api_set_node_name("node-1", {"name": "Updated Node"})

    assert result["ok"] is True
    assert result["node"]["name"] == "Updated Node"
    assert settings.DEVICE_REGISTRY[0]["rooms"][0]["nodes"][0]["name"] == "Updated Node"
    assert motion_stub.updates == [("node-1", "Updated Node")]

    written = json.loads(settings.REGISTRY_FILE.read_text())
    assert written[0]["rooms"][0]["nodes"][0]["name"] == "Updated Node"

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name("node-1", {"name": "   "})

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name("node-1", {"name": "x" * 200})

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name("missing", {"name": "Another"})


def test_api_reorder_rooms(monkeypatch, tmp_path):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    test_registry = [
        {
            "id": "house",
            "name": "House",
            "rooms": [
                {"id": "room-a", "name": "Room A", "nodes": []},
                {"id": "room-b", "name": "Room B", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    result = routes_api.api_reorder_rooms("house", {"order": ["room-b", "room-a"]})
    assert result["ok"] is True
    assert result["order"] == ["room-b", "room-a"]
    stored = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in stored] == ["room-b", "room-a"]

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms("house", {"order": ["room-a"]})
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms("house", {"order": "room-a"})
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms("missing", {"order": ["room-a", "room-b"]})
    assert excinfo.value.status_code == 404
