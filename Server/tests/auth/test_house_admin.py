import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.auth.models import HouseMembership, RoomAccess, User
from app.auth.security import SESSION_COOKIE_NAME
from app.auth.service import create_user, init_auth_storage
from app.config import settings


class _NoopBus:
    def __getattr__(self, name: str):  # pragma: no cover - simple stub
        def _noop(*args, **kwargs):
            return None

        return _noop


def _build_registry() -> list[dict]:
    return [
        {
            "id": "alpha",
            "name": "Alpha House",
            "external_id": "alpha-public",
            "rooms": [
                {
                    "id": "alpha-room",
                    "name": "Alpha Room",
                    "nodes": [],
                },
                {
                    "id": "alpha-denied",
                    "name": "Alpha Hidden",
                    "nodes": [],
                },
            ],
        },
        {
            "id": "beta",
            "name": "Beta House",
            "external_id": "beta-public",
            "rooms": [
                {
                    "id": "beta-room",
                    "name": "Beta Room",
                    "nodes": [],
                }
            ],
        },
    ]


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import app.mqtt_bus
    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())

    import app.motion
    import app.registry as registry_module
    import app.status_monitor
    from app.main import app as fastapi_app
    monkeypatch.setattr(app.motion.motion_manager, "start", lambda: None)
    monkeypatch.setattr(app.motion.motion_manager, "stop", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "start", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "stop", lambda: None)

    original_url = settings.AUTH_DB_URL
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)

    registry_data = _build_registry()
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(registry_data))
    monkeypatch.setattr(settings, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(registry_data))
    monkeypatch.setattr(registry_module.settings, "DEVICE_REGISTRY", deepcopy(registry_data))
    monkeypatch.setattr(registry_module.settings, "REGISTRY_FILE", registry_file)
    registry_module.ensure_house_external_ids(persist=False)

    init_auth_storage()

    try:
        with TestClient(fastapi_app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        database.reset_session_factory(original_url)


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert SESSION_COOKIE_NAME in response.cookies


def _create_server_admin(username: str, password: str) -> None:
    with database.SessionLocal() as session:
        create_user(session, username, password, server_admin=True)


def _membership_rooms(session, membership_id: int) -> set[str]:
    rows = session.exec(
        select(RoomAccess).where(RoomAccess.membership_id == membership_id)
    ).all()
    return {row.room_id for row in rows}


def test_create_guest_account_and_prevent_duplicates(client: TestClient):
    _create_server_admin("admin", "admin-pass")
    _login(client, "admin", "admin-pass")

    create_response = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "alpha-guest",
            "password": "guest-pass",
            "role": "guest",
            "rooms": ["alpha-room"],
        },
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["username"] == "alpha-guest"
    assert body["role"] == "guest"
    assert body["serverAdmin"] is False
    assert body["rooms"] == [{"id": "alpha-room", "name": "Alpha Room"}]

    duplicate = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "alpha-guest",
            "password": "another-pass",
            "role": "guest",
        },
    )
    assert duplicate.status_code == 409

    with database.SessionLocal() as session:
        membership = session.exec(
            select(HouseMembership)
            .join(User, User.id == HouseMembership.user_id)
            .where(User.username == "alpha-guest")
        ).one()
        assert _membership_rooms(session, membership.id) == {"alpha-room"}


def test_role_changes_and_room_constraints(client: TestClient):
    _create_server_admin("admin", "admin-pass")
    _login(client, "admin", "admin-pass")

    create_response = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "switch-user",
            "password": "guest-pass",
            "role": "guest",
            "rooms": ["alpha-room"],
        },
    )
    assert create_response.status_code == 201
    member_data = create_response.json()
    membership_id = member_data["membershipId"]

    promote = client.patch(
        f"/api/house-admin/alpha-public/members/{membership_id}",
        json={"role": "admin"},
    )
    assert promote.status_code == 200
    promote_body = promote.json()
    assert promote_body["role"] == "admin"
    assert promote_body["rooms"] == []

    with database.SessionLocal() as session:
        assert _membership_rooms(session, membership_id) == set()

    downgrade = client.patch(
        f"/api/house-admin/alpha-public/members/{membership_id}",
        json={"role": "guest", "rooms": ["alpha-room"]},
    )
    assert downgrade.status_code == 200
    downgrade_body = downgrade.json()
    assert downgrade_body["role"] == "guest"
    assert downgrade_body["rooms"] == [{"id": "alpha-room", "name": "Alpha Room"}]

    cross_house = client.patch(
        f"/api/house-admin/alpha-public/members/{membership_id}",
        json={"rooms": ["beta-room"]},
    )
    assert cross_house.status_code == 422

    wrong_house = client.patch(
        f"/api/house-admin/beta-public/members/{membership_id}",
        json={"role": "guest"},
    )
    assert wrong_house.status_code == 404


def test_invalid_room_rejected_on_create(client: TestClient):
    _create_server_admin("admin", "admin-pass")
    _login(client, "admin", "admin-pass")

    response = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "invalid-room",
            "password": "guest-pass",
            "role": "guest",
            "rooms": ["unknown-room"],
        },
    )
    assert response.status_code == 422
