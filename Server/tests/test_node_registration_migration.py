import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database, node_credentials
import app.registry as registry_module
from app.auth.models import NodeCredential as NodeCredentialModel, NodeRegistration
from app.auth.service import init_auth_storage
from app.config import settings


@pytest.fixture()
def auth_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_db_url = settings.AUTH_DB_URL
    original_registry_file = settings.REGISTRY_FILE

    original_save_registry = registry_module.save_registry

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", [])
    registry_file = tmp_path / "registry.json"
    monkeypatch.setattr(settings, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(registry_module.settings, "DEVICE_REGISTRY", [])
    monkeypatch.setattr(registry_module.settings, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(registry_module, "save_registry", lambda: None)

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)

    init_auth_storage()

    yield

    database.reset_session_factory(original_db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", original_db_url)
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(registry_module.settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(settings, "REGISTRY_FILE", original_registry_file)
    monkeypatch.setattr(registry_module.settings, "REGISTRY_FILE", original_registry_file)
    monkeypatch.setattr(registry_module, "save_registry", original_save_registry)


def test_migrate_credentials_creates_registration(auth_db):
    created_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    token_issued_at = datetime(2024, 1, 2, 8, 30, tzinfo=timezone.utc)

    with database.SessionLocal() as session:
        credential = NodeCredentialModel(
            node_id="legacy-node",
            house_slug="alpha",
            room_id="living",
            display_name="Legacy Node",
            download_id="download-123",
            token_hash="hash-abc",
            created_at=created_at,
            token_issued_at=token_issued_at,
            provisioned_at=None,
            certificate_fingerprint="ff:aa",
            certificate_pem_path="/data/certs/legacy-node.pem",
            private_key_pem_path="/data/keys/legacy-node.key",
        )
        session.add(credential)
        session.commit()

        created = node_credentials.migrate_credentials_to_registrations(session)
        assert created == 1

        registration = node_credentials.get_registration_by_node_id(session, "legacy-node")
        assert registration is not None
        assert registration.download_id == "download-123"
        assert registration.token_hash == "hash-abc"
        assert registration.token_issued_at == _naive(token_issued_at)
        assert registration.house_slug == "alpha"
        assert registration.room_id == "living"
        assert registration.display_name == "Legacy Node"
        assert registration.assigned_at == _naive(created_at)
        assert registration.provisioned_at is None
        assert registration.certificate_fingerprint == "ff:aa"
        assert registration.certificate_pem_path == "/data/certs/legacy-node.pem"
        assert registration.private_key_pem_path == "/data/keys/legacy-node.key"


def test_migrate_credentials_updates_existing_registration(auth_db):
    created_at = datetime(2024, 3, 5, 9, 15, tzinfo=timezone.utc)
    token_issued_at = datetime(2024, 3, 5, 10, 0, tzinfo=timezone.utc)

    with database.SessionLocal() as session:
        registration = NodeRegistration(
            node_id="existing-node",
            download_id="old-download",
            token_hash="old-hash",
            hardware_metadata={},
        )
        session.add(registration)
        session.commit()

        credential = NodeCredentialModel(
            node_id="existing-node",
            house_slug="beta",
            room_id="den",
            display_name="Existing Node",
            download_id="new-download",
            token_hash="new-hash",
            created_at=created_at,
            token_issued_at=token_issued_at,
            provisioned_at=created_at,
            certificate_fingerprint="11:22",
            certificate_pem_path="/data/certs/existing.pem",
            private_key_pem_path="/data/keys/existing.key",
        )
        session.add(credential)
        session.commit()

        created = node_credentials.migrate_credentials_to_registrations(session)
        assert created == 0

        updated = node_credentials.get_registration_by_node_id(session, "existing-node")
        assert updated is not None
        assert updated.download_id == "new-download"
        assert updated.token_hash == "new-hash"
        assert updated.token_issued_at == _naive(token_issued_at)
        assert updated.provisioned_at == _naive(created_at)
        assert updated.house_slug == "beta"
        assert updated.room_id == "den"
        assert updated.display_name == "Existing Node"
        assert updated.assigned_at == _naive(created_at)
        assert updated.certificate_fingerprint == "11:22"
        assert updated.certificate_pem_path == "/data/certs/existing.pem"
        assert updated.private_key_pem_path == "/data/keys/existing.key"
def _naive(dt: datetime) -> datetime:
    """Return a naive datetime for SQLite comparisons."""

    return dt.replace(tzinfo=None)


