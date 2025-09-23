from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import registry
from app.auth.passwords import hash_password, verify_password
from app.auth.service import create_user, init_auth_storage
from app.auth.models import User
from app.config import settings
from app.database import SessionLocal, reset_session_factory


def test_hash_and_verify_password() -> None:
    password = "s3cret-value"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong", hashed)


def test_init_auth_storage_seeds_admin(tmp_path, monkeypatch) -> None:
    original_url = settings.AUTH_DB_URL
    db_path = Path(tmp_path) / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(settings, "INITIAL_ADMIN_USERNAME", "seed-admin")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "ultra-secret")

    reset_session_factory(db_url)
    try:
        init_auth_storage()
        with SessionLocal() as session:
            admin = session.exec(select(User).where(User.server_admin.is_(True))).one()
            assert admin.username == "seed-admin"
            assert admin.hashed_password != settings.INITIAL_ADMIN_PASSWORD
            assert verify_password("ultra-secret", admin.hashed_password)

            guest = create_user(session, "guest-user", "guest-pass")
            assert guest.id is not None
            assert verify_password("guest-pass", guest.hashed_password)
    finally:
        reset_session_factory(original_url)


def test_generate_house_external_ids_random_and_unique(monkeypatch) -> None:
    sample_registry = [
        {"id": "alpha", "name": "Alpha"},
        {"id": "beta", "name": "Beta"},
    ]

    changed = registry.ensure_house_external_ids(sample_registry, persist=False)
    assert changed is True

    identifiers = [entry.get("external_id") for entry in sample_registry]
    assert len(identifiers) == len(set(identifiers))

    for entry, external_id in zip(sample_registry, identifiers):
        assert isinstance(external_id, str) and external_id
        assert 8 <= len(external_id) <= settings.MAX_HOUSE_ID_LENGTH
        assert external_id != entry["id"]
        assert all(ch in registry.EXTERNAL_ID_ALPHABET for ch in external_id)

    unchanged = registry.ensure_house_external_ids(sample_registry, persist=False)
    assert unchanged is False
