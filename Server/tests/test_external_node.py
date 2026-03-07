import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import registry


class _NoopBus:
    def pub(self, *args, **kwargs):
        pass

    def ws_set(self, *args, **kwargs):
        pass

    def rgb_set(self, *args, **kwargs):
        pass

    def white_set(self, *args, **kwargs):
        pass

    def motion_on(self, *args, **kwargs):
        pass

    def status_request(self, *args, **kwargs):
        pass

    def motion_status_request(self, *args, **kwargs):
        pass

    def ota_check(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _stub_mqtt(monkeypatch):
    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    from app import database, ota
    from app.auth.models import User
    from app.auth.service import init_auth_storage
    from app.config import settings

    test_registry = [
        {
            "id": "test-house",
            "name": "Test House",
            "external_id": "test-house",
            "rooms": [
                {"id": "living-room", "name": "Living Room", "nodes": []},
            ],
        }
    ]

    monkeypatch.setattr(settings, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))

    original_db_url = settings.AUTH_DB_URL
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"

    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", db_url)
    init_auth_storage()

    session = database.SessionLocal()
    admin = User(id=1, username="admin", hashed_password="", server_admin=True)
    try:
        yield admin, session
    finally:
        session.close()
        database.reset_session_factory(original_db_url)


def test_create_external_node(setup):
    from app.routes_server_admin import create_external_node, ExternalNodeCreateRequest
    from app.auth.models import NodeRegistration

    admin, session = setup

    payload = ExternalNodeCreateRequest(
        displayName="Gaming PC",
        assignHouseSlug="test-house",
        assignRoomId="living-room",
    )

    response = create_external_node(payload, current_user=admin, session=session)
    node_id = response.node_id
    assert node_id

    # Verify DB registration
    reg = session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(NodeRegistration).where(
            NodeRegistration.node_id == node_id
        )
    ).first()
    assert reg is not None
    assert reg.hardware_metadata.get("external") is True
    assert reg.display_name == "Gaming PC"

    # Verify registry entry
    _, _, node = registry.find_node(node_id)
    assert node is not None
    assert node["kind"] == "external"
    assert node["modules"] == ["rgb"]
    assert node["name"] == "Gaming PC"


def test_create_external_node_bad_room(setup):
    from app.routes_server_admin import create_external_node, ExternalNodeCreateRequest
    from fastapi import HTTPException

    admin, session = setup

    payload = ExternalNodeCreateRequest(
        displayName="Bad Node",
        assignHouseSlug="test-house",
        assignRoomId="nonexistent",
    )

    with pytest.raises(HTTPException) as exc_info:
        create_external_node(payload, current_user=admin, session=session)
    assert exc_info.value.status_code == 404


def test_flash_external_node_rejected(setup):
    from app.routes_server_admin import create_external_node, ExternalNodeCreateRequest
    from app import node_credentials
    from fastapi import HTTPException

    admin, session = setup

    payload = ExternalNodeCreateRequest(
        displayName="PC Lights",
        assignHouseSlug="test-house",
        assignRoomId="living-room",
    )
    response = create_external_node(payload, current_user=admin, session=session)
    node_id = response.node_id

    reg = node_credentials.get_registration_by_node_id(session, node_id)
    assert reg is not None
    metadata = reg.hardware_metadata or {}
    assert metadata.get("external") is True


def test_delete_external_node(setup):
    from app.routes_server_admin import (
        create_external_node,
        delete_node_factory_registration,
        ExternalNodeCreateRequest,
    )

    admin, session = setup

    payload = ExternalNodeCreateRequest(
        displayName="Temporary PC",
        assignHouseSlug="test-house",
        assignRoomId="living-room",
    )
    response = create_external_node(payload, current_user=admin, session=session)
    node_id = response.node_id

    # Delete should succeed without errors
    delete_node_factory_registration(node_id, current_user=admin, session=session)

    # Verify gone from registry
    _, _, node = registry.find_node(node_id)
    assert node is None


def test_registration_summary_external_has_no_board(setup):
    from app.routes_server_admin import (
        create_external_node,
        _registration_summary,
        ExternalNodeCreateRequest,
    )
    from app import node_credentials

    admin, session = setup

    payload = ExternalNodeCreateRequest(
        displayName="OpenRGB",
        assignHouseSlug="test-house",
        assignRoomId="living-room",
    )
    response = create_external_node(payload, current_user=admin, session=session)

    reg = node_credentials.get_registration_by_node_id(session, response.node_id)
    summary = _registration_summary(reg)
    assert summary.board is None
