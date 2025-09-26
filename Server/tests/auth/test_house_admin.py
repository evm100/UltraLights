import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.auth.models import (
    House,
    HouseMembership,
    HouseRole,
    NodeRegistration,
    RoomAccess,
    User,
)
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
    import app.account_linker as account_linker_module
    import app.status_monitor
    from app.main import app as fastapi_app
    monkeypatch.setattr(app.motion.motion_manager, "start", lambda: None)
    monkeypatch.setattr(app.motion.motion_manager, "stop", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "start", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "stop", lambda: None)
    monkeypatch.setattr(account_linker_module.account_linker, "start", lambda: None)
    monkeypatch.setattr(account_linker_module.account_linker, "stop", lambda: None)

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


def _create_house_member(
    username: str,
    password: str,
    *,
    house_external_id: str,
    role: HouseRole,
) -> None:
    with database.SessionLocal() as session:
        house = session.exec(
            select(House).where(House.external_id == house_external_id)
        ).first()
        assert house is not None, f"House {house_external_id} missing in database"
        user = create_user(session, username, password, server_admin=False)
        membership = HouseMembership(user_id=user.id, house_id=house.id, role=role)
        session.add(membership)
        session.commit()


def _create_house_admin(username: str, password: str, *, house_external_id: str) -> None:
    _create_house_member(
        username,
        password,
        house_external_id=house_external_id,
        role=HouseRole.ADMIN,
    )


def _create_house_guest(username: str, password: str, *, house_external_id: str) -> None:
    _create_house_member(
        username,
        password,
        house_external_id=house_external_id,
        role=HouseRole.GUEST,
    )


def _create_house_admin_user(
    username: str, password: str, *, house_external_id: str
) -> User:
    with database.SessionLocal() as session:
        house = session.exec(
            select(House).where(House.external_id == house_external_id)
        ).first()
        assert house is not None, "house missing in database"
        user = create_user(session, username, password, server_admin=False)
        membership = HouseMembership(user_id=user.id, house_id=house.id, role=HouseRole.ADMIN)
        session.add(membership)
        session.commit()
        session.refresh(user)
        return user


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


def test_house_admin_can_manage_memberships(client: TestClient):
    _create_house_admin("alpha-manager", "manager-pass", house_external_id="alpha-public")
    _login(client, "alpha-manager", "manager-pass")

    create_response = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "managed-guest",
            "password": "guest-pass",
            "role": "guest",
            "rooms": ["alpha-room"],
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["role"] == "guest"
    assert {room["id"] for room in created["rooms"]} == {"alpha-room"}
    membership_id = created["membershipId"]

    update_response = client.patch(
        f"/api/house-admin/alpha-public/members/{membership_id}",
        json={"rooms": ["alpha-room", "alpha-denied"]},
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["role"] == "guest"
    assert {room["id"] for room in updated["rooms"]} == {"alpha-room", "alpha-denied"}

    with database.SessionLocal() as session:
        membership = session.exec(
            select(HouseMembership).where(HouseMembership.id == membership_id)
        ).one()
        assert membership.role == HouseRole.GUEST
        assert _membership_rooms(session, membership_id) == {"alpha-room", "alpha-denied"}


def test_house_guest_cannot_manage_memberships(client: TestClient):
    _create_house_guest("alpha-guest-user", "guest-pass", house_external_id="alpha-public")
    _login(client, "alpha-guest-user", "guest-pass")

    response = client.post(
        "/api/house-admin/alpha-public/members",
        json={
            "username": "another-user",
            "password": "temp-pass",
            "role": "guest",
            "rooms": ["alpha-room"],
        },
    )
    assert response.status_code == 403


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


def test_house_admin_assigns_pending_node_to_room(client: TestClient):
    admin_user = _create_house_admin_user(
        "alpha-admin", "admin-pass", house_external_id="alpha-public"
    )
    _login(client, "alpha-admin", "admin-pass")

    import app.registry as registry_module

    with database.SessionLocal() as session:
        house = session.exec(
            select(House).where(House.external_id == "alpha-public")
        ).first()
        assert house is not None
        registration = NodeRegistration(
            node_id="alpha-node-new",
            download_id="alpha-download",
            token_hash=registry_module.hash_node_token("pending-token"),
            provisioning_token="pending-token",
            assigned_user_id=admin_user.id,
            assigned_house_id=house.id,
            assigned_at=datetime.now(timezone.utc),
        )
        session.add(registration)
        session.commit()

    response = client.post(
        "/api/house/alpha-public/nodes/assign",
        json={
            "nodeId": "alpha-node-new",
            "roomId": "alpha-room",
            "name": "Kitchen Node",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["roomId"] == "alpha-room"
    assert body["node"]["name"] == "Kitchen Node"

    with database.SessionLocal() as session:
        stored = session.exec(
            select(NodeRegistration).where(NodeRegistration.node_id == "alpha-node-new")
        ).one()
        assert stored.room_id == "alpha-room"
        assert stored.house_slug == "alpha"
        assert stored.display_name == "Kitchen Node"

    house, room, node = registry_module.find_node("alpha-node-new")
    assert room is not None
    assert room.get("id") == "alpha-room"
    assert node and node.get("name") == "Kitchen Node"


def test_house_admin_moves_node_between_rooms(client: TestClient):
    admin_user = _create_house_admin_user(
        "alpha-admin", "admin-pass", house_external_id="alpha-public"
    )
    _login(client, "alpha-admin", "admin-pass")

    import app.registry as registry_module

    with database.SessionLocal() as session:
        house = session.exec(
            select(House).where(House.external_id == "alpha-public")
        ).first()
        assert house is not None
        registration = NodeRegistration(
            node_id="alpha-node-existing",
            download_id="alpha-existing",
            token_hash=registry_module.hash_node_token("existing-token"),
            provisioning_token="existing-token",
            assigned_user_id=admin_user.id,
            assigned_house_id=house.id,
            house_slug="alpha",
            room_id="alpha-room",
            display_name="Existing Node",
            assigned_at=datetime.now(timezone.utc),
        )
        session.add(registration)
        session.commit()

    registry_module.place_node_in_room(
        "alpha-node-existing", "alpha", "alpha-room", name="Existing Node"
    )

    response = client.post(
        "/api/house/alpha-public/nodes/alpha-node-existing/move",
        json={"roomId": "alpha-denied", "name": "Moved Node"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["roomId"] == "alpha-denied"
    assert body["node"]["name"] == "Moved Node"

    with database.SessionLocal() as session:
        stored = session.exec(
            select(NodeRegistration).where(
                NodeRegistration.node_id == "alpha-node-existing"
            )
        ).one()
        assert stored.room_id == "alpha-denied"
        assert stored.house_slug == "alpha"
        assert stored.display_name == "Moved Node"

    house, room, node = registry_module.find_node("alpha-node-existing")
    assert room is not None
    assert room.get("id") == "alpha-denied"
    assert node and node.get("name") == "Moved Node"
