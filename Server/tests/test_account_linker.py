from copy import deepcopy
import sys
from pathlib import Path

import pytest
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database, node_credentials, registry
from app.account_linker import AccountLinker
from app.auth.models import (
    House,
    HouseMembership,
    HouseRole,
    NodeCredential as NodeCredentialModel,
    NodeRegistration,
)
from app.auth.service import create_user, init_auth_storage
from app.config import settings


@pytest.fixture()
def account_env(tmp_path, monkeypatch):
    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_db_url = settings.AUTH_DB_URL

    test_registry = [
        {
            "id": "test-house",
            "name": "Test House",
            "external_id": "test-house",
            "rooms": [],
        }
    ]

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", settings.DEVICE_REGISTRY)
    monkeypatch.setattr(registry, "save_registry", lambda: None)

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)

    init_auth_storage()

    yield

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", original_registry)
    database.reset_session_factory(original_db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", original_db_url)


def _create_membership(session, user_id: int) -> int:
    house = session.exec(select(House).where(House.external_id == "test-house")).first()
    if house is None:
        house = House(display_name="Test House", external_id="test-house")
        session.add(house)
        session.commit()
        session.refresh(house)
    membership = HouseMembership(user_id=user_id, house_id=house.id, role=HouseRole.ADMIN)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership.house_id


def _create_registration(session) -> NodeRegistration:
    batch = node_credentials.create_batch(session, 1)
    return batch[0].registration


def test_handle_credentials_links_user(account_env):
    linker = AccountLinker()
    with database.SessionLocal() as session:
        user = create_user(session, "alice", "wonderland", server_admin=False)
        house_id = _create_membership(session, user.id)
        registration = _create_registration(session)
        node_id = registration.node_id
        # ensure registration persisted for assertions later
        session.refresh(registration)
        user_id = user.id
    result = linker.handle_credentials(node_id, "alice", "wonderland")
    assert result is not None
    with database.SessionLocal() as session:
        refreshed = node_credentials.get_registration_by_node_id(session, node_id)
        assert refreshed is not None
        assert refreshed.assigned_user_id == user_id
        assert refreshed.assigned_house_id == house_id
        assert refreshed.account_username == "alice"
        assert refreshed.account_password_hash is not None
        assert refreshed.account_credentials_received_at is not None
        assert refreshed.account_password_hash != "wonderland"


def test_handle_credentials_rejects_invalid_password(account_env):
    linker = AccountLinker()
    with database.SessionLocal() as session:
        user = create_user(session, "bob", "builder", server_admin=False)
        _create_membership(session, user.id)
        registration = _create_registration(session)
        node_id = registration.node_id
        session.refresh(registration)
    result = linker.handle_credentials(node_id, "bob", "wrongpass")
    assert result is None
    with database.SessionLocal() as session:
        refreshed = node_credentials.get_registration_by_node_id(session, node_id)
        assert refreshed is not None
        assert refreshed.account_username is None
        assert refreshed.assigned_user_id is None


def test_handle_credentials_migrates_between_users(account_env):
    linker = AccountLinker()
    with database.SessionLocal() as session:
        legacy_owner = create_user(session, "legacy-owner", "oldpass", server_admin=False)
        new_owner = create_user(session, "new-owner", "newpass", server_admin=False)
        legacy_owner_id = legacy_owner.id
        new_owner_id = new_owner.id

        legacy_house = House(display_name="Legacy House", external_id="legacy-house")
        session.add(legacy_house)
        session.commit()
        session.refresh(legacy_house)

        membership_legacy = HouseMembership(
            user_id=legacy_owner.id,
            house_id=legacy_house.id,
            role=HouseRole.ADMIN,
        )
        session.add(membership_legacy)
        session.commit()

        new_house_id = _create_membership(session, new_owner_id)

        registration = _create_registration(session)
        node_id = registration.node_id
        registration.assigned_user_id = legacy_owner_id
        registration.assigned_house_id = legacy_house.id
        registration.house_slug = legacy_house.external_id
        registration.room_id = "living"
        registration.display_name = "Legacy Node"
        session.add(registration)
        session.commit()
        session.refresh(registration)

        credential_row = NodeCredentialModel(
            node_id=node_id,
            house_slug=legacy_house.external_id,
            room_id="living",
            display_name="Legacy Node",
            download_id=registration.download_id,
            token_hash=registration.token_hash,
        )
        session.add(credential_row)
        session.commit()

    settings.DEVICE_REGISTRY.append(
        {
            "id": "legacy-house",
            "name": "Legacy House",
            "external_id": "legacy-house",
            "rooms": [
                {
                    "id": "living",
                    "name": "Living Room",
                    "nodes": [
                        {"id": node_id, "name": "Legacy Node"},
                    ],
                }
            ],
        }
    )

    result = linker.handle_credentials(node_id, "new-owner", "newpass")
    assert result is not None

    with database.SessionLocal() as session:
        refreshed = node_credentials.get_registration_by_node_id(session, node_id)
        assert refreshed is not None
        assert refreshed.assigned_user_id == new_owner_id
        assert refreshed.assigned_house_id == new_house_id
        assert refreshed.room_id is None
        assert refreshed.house_slug == "test-house"
        assert node_credentials.get_by_node_id(session, node_id) is None

    house_entry, room_entry, node_entry = registry.find_node(node_id)
    assert house_entry is not None
    assert registry.get_house_external_id(house_entry) == "test-house"
    assert room_entry is None
    assert isinstance(node_entry, dict)
    assert node_entry.get("id") == node_id

    legacy_entry = next(
        (house for house in settings.DEVICE_REGISTRY if house.get("id") == "legacy-house"),
        None,
    )
    if legacy_entry:
        for room in legacy_entry.get("rooms", []):
            for node in room.get("nodes", []) or []:
                assert node.get("id") != node_id


def test_handle_credentials_recreates_missing_registration(account_env):
    linker = AccountLinker()
    with database.SessionLocal() as session:
        owner = create_user(session, "recover", "secret", server_admin=False)
        house_id = _create_membership(session, owner.id)
        user_id = owner.id

        registration = _create_registration(session)
        node_id = registration.node_id
        download_id = registration.download_id
        token_hash = registration.token_hash
        session.refresh(registration)

        credential_row = NodeCredentialModel(
            node_id=node_id,
            house_slug="test-house",
            room_id="den",
            display_name="Recovered Node",
            download_id=download_id,
            token_hash=token_hash,
        )
        session.add(credential_row)
        session.commit()

        session.delete(registration)
        session.commit()

    settings.DEVICE_REGISTRY[0]["rooms"] = [
        {
            "id": "den",
            "name": "Den",
            "nodes": [
                {"id": node_id, "name": "Recovered Node"},
            ],
        }
    ]

    result = linker.handle_credentials(node_id, "recover", "secret")
    assert result is not None

    with database.SessionLocal() as session:
        refreshed = node_credentials.get_registration_by_node_id(session, node_id)
        assert refreshed is not None
        assert refreshed.assigned_user_id == user_id
        assert refreshed.assigned_house_id == house_id
        assert refreshed.room_id is None
        assert refreshed.house_slug == "test-house"
        assert node_credentials.get_by_node_id(session, node_id) is None

    house_entry, room_entry, node_entry = registry.find_node(node_id)
    assert house_entry is not None
    assert registry.get_house_external_id(house_entry) == "test-house"
    assert room_entry is None
    assert isinstance(node_entry, dict)
    assert node_entry.get("id") == node_id


def test_handle_credentials_after_deletion(account_env):
    linker = AccountLinker()
    with database.SessionLocal() as session:
        legacy_owner = create_user(session, "legacy", "oldpass", server_admin=False)
        new_owner = create_user(session, "return", "newpass", server_admin=False)
        new_owner_id = new_owner.id

        legacy_house = House(display_name="Legacy House", external_id="legacy-house")
        session.add(legacy_house)
        session.commit()
        session.refresh(legacy_house)

        membership_legacy = HouseMembership(
            user_id=legacy_owner.id,
            house_id=legacy_house.id,
            role=HouseRole.ADMIN,
        )
        session.add(membership_legacy)
        session.commit()

        new_house_id = _create_membership(session, new_owner.id)

        registration = _create_registration(session)
        node_id = registration.node_id
        registration.assigned_user_id = legacy_owner.id
        registration.assigned_house_id = legacy_house.id
        registration.house_slug = legacy_house.external_id
        registration.room_id = "office"
        registration.display_name = "Returning Node"
        registration.assigned_at = registration.created_at
        session.add(registration)
        session.commit()
        session.refresh(registration)

        credential_row = NodeCredentialModel(
            node_id=node_id,
            house_slug=legacy_house.external_id,
            room_id="office",
            display_name="Returning Node",
            download_id=registration.download_id,
            token_hash=registration.token_hash,
        )
        session.add(credential_row)
        session.commit()

        node_credentials.delete_credentials(session, node_id)

        refreshed = node_credentials.get_registration_by_node_id(session, node_id)
        assert refreshed is not None
        assert refreshed.assigned_user_id is None
        assert refreshed.assigned_house_id is None
        assert refreshed.house_slug is None
        assert refreshed.room_id is None
        assert refreshed.account_username is None
        assert refreshed.account_password_hash is None
        assert refreshed.account_credentials_received_at is None
        assert refreshed.assigned_at is None
        assert node_credentials.get_by_node_id(session, node_id) is None

    result = linker.handle_credentials(node_id, "return", "newpass")
    assert result is not None

    with database.SessionLocal() as session:
        reassigned = node_credentials.get_registration_by_node_id(session, node_id)
        assert reassigned is not None
        assert reassigned.assigned_user_id == new_owner_id
        assert reassigned.assigned_house_id == new_house_id
        assert reassigned.room_id is None
        assert reassigned.house_slug == "test-house"
        assert node_credentials.get_by_node_id(session, node_id) is None

    house_entry, room_entry, node_entry = registry.find_node(node_id)
    assert house_entry is not None
    assert registry.get_house_external_id(house_entry) == "test-house"
    assert room_entry is None
    assert isinstance(node_entry, dict)
    assert node_entry.get("id") == node_id
