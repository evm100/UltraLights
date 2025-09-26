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
from app.auth.models import House, HouseMembership, HouseRole, NodeRegistration
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
