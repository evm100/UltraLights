import importlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import List

import pytest
from fastapi import HTTPException

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


class _StubSession:
    def exec(self, *_args, **_kwargs):  # pragma: no cover - simple stub
        class _Result:
            def all(self_inner):  # pragma: no cover - simple stub
                return []

        return _Result()


@pytest.fixture(autouse=True)
def _stub_mqtt(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())


@pytest.fixture()
def admin_user_session():
    from app.auth.models import User

    return User(id=1, username="admin", hashed_password="", server_admin=True), _StubSession()


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


def test_api_delete_room_cleans_up(monkeypatch, tmp_path, admin_user_session):
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

    user, session = admin_user_session

    result = routes_api.api_delete_room(
        "house", "room-a", current_user=user, session=session
    )

    assert result["ok"] is True
    assert result["room"]["id"] == "room-a"
    assert result["removed_nodes"] == ["node-1", "node-2"]

    remaining = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in remaining] == ["room-b"]

    assert manager.node_calls == ["node-1", "node-2"]
    assert manager.room_calls == [("house", "room-a")]
    assert status.calls == ["node-1", "node-2"]
    assert schedule.calls == [("house", "room-a")]


def test_api_delete_room_missing(monkeypatch, admin_user_session):
    import app.routes_api as routes_api
    from app.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", [
        {"id": "house", "rooms": []}
    ])

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_delete_room(
            "house", "room-a", current_user=admin_user_session[0], session=admin_user_session[1]
        )

    assert excinfo.value.status_code == 404


def test_api_add_node_house_prefixed_id(monkeypatch, tmp_path, admin_user_session):
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

    user, session = admin_user_session
    result = routes_api.api_add_node(
        "del-sur",
        "kitchen",
        {"name": "Kitchen Node"},
        current_user=user,
        session=session,
    )

    assert result["ok"] is True
    assert result["node"]["id"] == "del-sur-kitchen-node"
    stored_nodes = settings.DEVICE_REGISTRY[0]["rooms"][0]["nodes"]
    assert stored_nodes[0]["id"] == "del-sur-kitchen-node"


def test_api_add_node_duplicate_name(monkeypatch, tmp_path, admin_user_session):
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
        routes_api.api_add_node(
            "del-sur",
            "kitchen",
            {"name": "Kitchen Node"},
            current_user=admin_user_session[0],
            session=admin_user_session[1],
        )

    assert excinfo.value.status_code == 400
    assert "already exists" in str(excinfo.value.detail)


def test_api_add_node_rejects_long_id(monkeypatch, tmp_path, admin_user_session):
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
        routes_api.api_add_node(
            "del-sur",
            "kitchen",
            {"name": "x" * 50},
            current_user=admin_user_session[0],
            session=admin_user_session[1],
        )

    assert excinfo.value.status_code == 400
    assert str(excinfo.value.detail) == "node id too long (max 31 characters)"


def test_api_set_node_name(monkeypatch, tmp_path, admin_user_session):
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

    user, session = admin_user_session
    result = routes_api.api_set_node_name(
        "node-1", {"name": "Updated Node"}, current_user=user, session=session
    )

    assert result["ok"] is True
    assert result["node"]["name"] == "Updated Node"
    assert settings.DEVICE_REGISTRY[0]["rooms"][0]["nodes"][0]["name"] == "Updated Node"
    assert motion_stub.updates == [("node-1", "Updated Node")]

    written = json.loads(settings.REGISTRY_FILE.read_text())
    assert written[0]["rooms"][0]["nodes"][0]["name"] == "Updated Node"

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name(
            "node-1", {"name": "   "}, current_user=user, session=session
        )

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name(
            "node-1", {"name": "x" * 200}, current_user=user, session=session
        )

    with pytest.raises(HTTPException):
        routes_api.api_set_node_name(
            "missing", {"name": "Another"}, current_user=user, session=session
        )


def test_api_reorder_rooms(monkeypatch, tmp_path, admin_user_session):
    import app.routes_api as routes_api
    from app.config import settings

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

    user, session = admin_user_session
    result = routes_api.api_reorder_rooms(
        "house", {"order": ["room-b", "room-a"]}, current_user=user, session=session
    )
    assert result["ok"] is True
    assert result["order"] == ["room-b", "room-a"]
    stored = settings.DEVICE_REGISTRY[0]["rooms"]
    assert [room["id"] for room in stored] == ["room-b", "room-a"]

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms(
            "house", {"order": ["room-a"]}, current_user=user, session=session
        )
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms(
            "house", {"order": "room-a"}, current_user=user, session=session
        )
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        routes_api.api_reorder_rooms(
            "missing",
            {"order": ["room-a", "room-b"]},
            current_user=user,
            session=session,
        )
    assert excinfo.value.status_code == 404


def test_api_reorder_room_presets(monkeypatch, tmp_path, admin_user_session):
    preset_path = tmp_path / "custom_presets.json"
    monkeypatch.setenv("CUSTOM_PRESET_FILE", str(preset_path))

    test_registry = [
        {
            "id": "house",
            "name": "House",
            "rooms": [{"id": "room", "name": "Room", "nodes": []}],
        }
    ]
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(test_registry))
    monkeypatch.setenv("REGISTRY_FILE", str(registry_path))

    for module_name in ["app.routes_api", "app.presets", "app.config", "app.registry"]:
        sys.modules.pop(module_name, None)

    try:
        config_module = importlib.import_module("app.config")
        settings = config_module.settings
        routes_api = importlib.import_module("app.routes_api")
        presets = importlib.import_module("app.presets")

        monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))
        monkeypatch.setattr(settings, "CUSTOM_PRESET_FILE", preset_path)
        monkeypatch.setattr(
            routes_api.registry.settings, "DEVICE_REGISTRY", deepcopy(test_registry)
        )
        monkeypatch.setattr(
            routes_api.registry.settings, "CUSTOM_PRESET_FILE", preset_path
        )
        assert routes_api.registry.find_room("house", "room")[1] is not None

        presets.save_custom_preset(
            "house",
            "room",
            {
                "id": "one",
                "name": "One",
                "actions": [
                    {"module": "white", "node": "node-1", "channel": 0, "effect": "", "params": []}
                ],
            },
        )
        presets.save_custom_preset(
            "house",
            "room",
            {
                "id": "two",
                "name": "Two",
                "actions": [
                    {"module": "white", "node": "node-1", "channel": 1, "effect": "", "params": []}
                ],
            },
        )

        user, session = admin_user_session
        result = routes_api.api_reorder_room_presets(
            "house",
            "room",
            {"order": ["two", "one"]},
            current_user=user,
            session=session,
        )
        assert result["ok"] is True
        assert [preset["id"] for preset in result["presets"]] == ["two", "one"]

        stored = presets.list_custom_presets("house", "room")
        assert [preset["id"] for preset in stored] == ["two", "one"]

        with pytest.raises(HTTPException) as excinfo:
            routes_api.api_reorder_room_presets(
                "house",
                "room",
                {"order": ["two", "two"]},
                current_user=user,
                session=session,
            )
        assert excinfo.value.status_code == 400

        with pytest.raises(HTTPException) as excinfo:
            routes_api.api_reorder_room_presets(
                "house", "room", {"order": "two"}, current_user=user, session=session
            )
        assert excinfo.value.status_code == 400

        with pytest.raises(HTTPException) as excinfo:
            routes_api.api_reorder_room_presets(
                "house",
                "room",
                {"order": ["missing", "one"]},
                current_user=user,
                session=session,
            )
        assert excinfo.value.status_code == 400
    finally:
        for module_name in ["app.routes_api", "app.presets", "app.config", "app.registry"]:
            sys.modules.pop(module_name, None)
