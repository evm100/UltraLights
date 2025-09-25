import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Iterable

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.auth.models import AuditLog, House, HouseMembership, HouseRole, RoomAccess, User
from app.auth.security import SESSION_COOKIE_NAME
from app.auth.service import create_user
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
                    "nodes": [
                        {"id": "alpha-node", "name": "Alpha Node", "modules": ["white"]}
                    ],
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
    from app.auth.service import init_auth_storage
    from app.main import app as fastapi_app

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

    monkeypatch.setattr(app.motion.motion_manager, "start", lambda: None)
    monkeypatch.setattr(app.motion.motion_manager, "stop", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "start", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "stop", lambda: None)

    init_auth_storage()

    try:
        with TestClient(fastapi_app, base_url="https://testserver") as client:
            yield client
    finally:
        database.reset_session_factory(original_url)


def _create_user(
    username: str,
    password: str,
    *,
    server_admin: bool = False,
    memberships: Iterable[tuple[str, HouseRole, Iterable[str] | None]] = (),
) -> None:
    with database.SessionLocal() as session:
        user = create_user(session, username, password, server_admin=server_admin)
        for house_external_id, role, rooms in memberships:
            house = session.exec(
                select(House).where(House.external_id == house_external_id)
            ).one()
            membership = HouseMembership(user_id=user.id, house_id=house.id, role=role)
            session.add(membership)
            session.commit()
            session.refresh(membership)
            if rooms:
                for room_id in rooms:
                    session.add(RoomAccess(membership_id=membership.id, room_id=room_id))
            session.commit()


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert SESSION_COOKIE_NAME in response.cookies


def _extract_nav_ids(html: str, attribute: str) -> set[str]:
    pattern = rf'data-{attribute}="([^"]+)"'
    return set(re.findall(pattern, html))


def _has_nav_marker(html: str, marker: str) -> bool:
    return f'data-{marker}' in html


def test_guest_restricted_to_assigned_rooms(client: TestClient):
    _create_user(
        "guest",
        "guest-pass",
        memberships=[("alpha-public", HouseRole.GUEST, ["alpha-room"])],
    )

    _login(client, "guest", "guest-pass")

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.text
    assert "Alpha House" in body
    assert "Beta House" not in body
    assert "Admin Panel" not in body
    assert "Server Admin" not in body
    assert _extract_nav_ids(body, "nav-house") == {"alpha-public"}
    nav_rooms = _extract_nav_ids(body, "nav-room-id")
    assert "alpha-room" in nav_rooms
    assert "alpha-denied" not in nav_rooms
    assert not _has_nav_marker(body, "nav-admin-link")
    assert not _has_nav_marker(body, "nav-server-admin-link")
    assert _has_nav_marker(body, "nav-logout")

    house_page = client.get("/house/alpha-public")
    assert house_page.status_code == 200
    house_nav_rooms = _extract_nav_ids(house_page.text, "nav-room-id")
    assert "alpha-room" in house_nav_rooms
    assert "alpha-denied" not in house_nav_rooms

    forbidden_room = client.get("/house/alpha-public/room/alpha-denied")
    assert forbidden_room.status_code == 403

    forbidden_house = client.get("/house/beta-public", follow_redirects=False)
    assert forbidden_house.status_code == 403


def test_guest_blocked_from_admin_and_api(client: TestClient):
    _create_user(
        "guest",
        "guest-pass",
        memberships=[("alpha-public", HouseRole.GUEST, ["alpha-room"])],
    )

    _login(client, "guest", "guest-pass")

    admin_page = client.get("/admin", follow_redirects=False)
    assert admin_page.status_code == 403

    api_response = client.post(
        "/api/house/alpha-public/rooms",
        json={"name": "Forbidden"},
    )
    assert api_response.status_code == 403


def test_house_admin_has_admin_access(client: TestClient):
    _create_user(
        "manager",
        "house-pass",
        memberships=[("alpha-public", HouseRole.ADMIN, None)],
    )

    _login(client, "manager", "house-pass")

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.text
    assert "Admin Panel" in body
    assert "Server Admin" not in body
    assert _has_nav_marker(body, "nav-admin-link")
    assert not _has_nav_marker(body, "nav-server-admin-link")
    assert "alpha-public" in _extract_nav_ids(body, "nav-house")
    assert "alpha-public" in _extract_nav_ids(body, "nav-house-admin")

    admin_house = client.get("/admin/house/alpha-public")
    assert admin_house.status_code == 200
    assert "Manage Rooms" in admin_house.text


def test_server_admin_sees_server_admin_navigation(client: TestClient):
    _create_user("root", "root-pass", server_admin=True)

    _login(client, "root", "root-pass")

    response = client.get("/admin")
    assert response.status_code == 200
    body = response.text
    assert "Admin Panel" in body
    assert _has_nav_marker(body, "nav-admin-link")
    assert _has_nav_marker(body, "nav-server-admin-link")

    server_admin_page = client.get("/server-admin")
    assert server_admin_page.status_code == 200
    server_body = server_admin_page.text
    assert "Server Administration" in server_body
    assert _has_nav_marker(server_body, "nav-server-admin-link")


def test_house_admin_blocked_from_server_admin(client: TestClient):
    _create_user(
        "manager",
        "house-pass",
        memberships=[("alpha-public", HouseRole.ADMIN, None)],
    )

    _login(client, "manager", "house-pass")

    page = client.get("/server-admin")
    assert page.status_code == 403

    response = client.post(
        "/api/server-admin/houses/alpha-public/rotate-id",
        json={"confirm": True},
    )
    assert response.status_code == 403


def test_server_admin_rotate_house_id(client: TestClient):
    _create_user("root", "root-pass", server_admin=True)

    _login(client, "root", "root-pass")

    response = client.post(
        "/api/server-admin/houses/alpha-public/rotate-id",
        json={"confirm": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["houseId"] == "alpha-public"
    assert payload["newId"] != "alpha-public"

    from app.config import settings as settings_module

    assert (
        settings_module.DEVICE_REGISTRY[0]["external_id"]
        == payload["newId"]
    )

    with database.SessionLocal() as session:
        house_row = session.exec(
            select(House).where(House.external_id == payload["newId"])
        ).one()
        assert house_row.display_name == "Alpha House"

        audit_row = session.exec(
            select(AuditLog).order_by(AuditLog.id.desc())
        ).first()
        assert audit_row is not None
        assert audit_row.action == "house_id_rotated"
        assert audit_row.data["new"] == payload["newId"]


def test_server_admin_create_house(client: TestClient):
    _create_user("root", "root-pass", server_admin=True)

    _login(client, "root", "root-pass")

    response = client.post(
        "/api/server-admin/houses",
        json={
            "id": "example-house",
            "name": "Example House",
            "rooms": [],
            "external_id": "",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == "example-house"
    assert payload["name"] == "Example House"
    assert payload["externalId"]

    assert any(
        house.get("id") == "example-house" for house in settings.DEVICE_REGISTRY
    )

    with database.SessionLocal() as session:
        house_row = session.exec(
            select(House).where(House.external_id == payload["externalId"])
        ).one()
        assert house_row.display_name == "Example House"

        audit_row = session.exec(
            select(AuditLog).order_by(AuditLog.id.desc())
        ).first()
        assert audit_row is not None
        assert audit_row.action == "house_created"
        assert audit_row.data["external_id"] == payload["externalId"]


def test_server_admin_create_house_admin(client: TestClient):
    _create_user("root", "root-pass", server_admin=True)

    _login(client, "root", "root-pass")

    response = client.post(
        "/api/server-admin/houses/alpha-public/admins",
        json={"username": "alpha-admin", "password": "secret"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["houseId"] == "alpha-public"
    assert payload["username"] == "alpha-admin"

    with database.SessionLocal() as session:
        user_row = session.exec(
            select(User).where(User.username == "alpha-admin")
        ).one()
        assert not user_row.server_admin
        membership_row = session.exec(
            select(HouseMembership)
            .join(House, House.id == HouseMembership.house_id)
            .where(HouseMembership.user_id == user_row.id)
        ).one()
        assert membership_row.role == HouseRole.ADMIN

        audit_row = session.exec(
            select(AuditLog).order_by(AuditLog.id.desc())
        ).first()
        assert audit_row is not None
        assert audit_row.action == "house_admin_created"


def test_non_admin_cannot_create_house_admin(client: TestClient):
    _create_user(
        "guest",
        "guest-pass",
        memberships=[("alpha-public", HouseRole.GUEST, ["alpha-room"])],
    )

    _login(client, "guest", "guest-pass")

    response = client.post(
        "/api/server-admin/houses/alpha-public/admins",
        json={"username": "blocked", "password": "secret"},
    )
    assert response.status_code == 403


def test_server_admin_remove_account(client: TestClient):
    _create_user("root", "root-pass", server_admin=True)
    _create_user(
        "alpha-admin",
        "secret",
        memberships=[("alpha-public", HouseRole.ADMIN, None)],
    )

    with database.SessionLocal() as session:
        target = session.exec(
            select(User).where(User.username == "alpha-admin")
        ).one()
        target_id = target.id
        membership_ids = session.exec(
            select(HouseMembership.id).where(HouseMembership.user_id == target_id)
        ).all()
        membership_ids = [mid for mid in membership_ids if mid is not None]

    _login(client, "root", "root-pass")

    response = client.delete(f"/api/server-admin/accounts/{target_id}")
    assert response.status_code == 204

    with database.SessionLocal() as session:
        deleted_user = session.exec(
            select(User).where(User.username == "alpha-admin")
        ).first()
        assert deleted_user is None
        remaining_memberships = session.exec(
            select(HouseMembership).where(HouseMembership.user_id == target_id)
        ).all()
        assert remaining_memberships == []
        for membership_id in membership_ids:
            accesses = session.exec(
                select(RoomAccess).where(RoomAccess.membership_id == membership_id)
            ).all()
            assert accesses == []
        audit_row = session.exec(
            select(AuditLog).order_by(AuditLog.id.desc())
        ).first()
        assert audit_row is not None
        assert audit_row.action == "account_removed"
        assert audit_row.data["user"] == "alpha-admin"


def test_server_admin_cannot_remove_last_admin(client: TestClient):
    _create_user("solo", "root-pass", server_admin=True)

    _login(client, "solo", "root-pass")

    with database.SessionLocal() as session:
        solo_id = session.exec(
            select(User.id).where(User.username == "solo")
        ).one()

    response = client.delete(f"/api/server-admin/accounts/{solo_id}")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "last server admin" in detail


def test_server_admin_cannot_remove_self_when_others_exist(
    client: TestClient,
):
    _create_user("root", "root-pass", server_admin=True)
    _create_user("other-admin", "secret", server_admin=True)

    _login(client, "root", "root-pass")

    with database.SessionLocal() as session:
        root_id = session.exec(select(User.id).where(User.username == "root")).one()

    response = client.delete(f"/api/server-admin/accounts/{root_id}")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "own account" in detail
