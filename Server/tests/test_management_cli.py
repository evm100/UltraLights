import sys
from pathlib import Path

import pytest
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.auth.models import AuditLog, House, HouseMembership, HouseRole, User
from app.auth.security import normalize_username
from app.config import settings


@pytest.fixture()
def cli_db(tmp_path):
    original_url = settings.AUTH_DB_URL
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    try:
        yield db_url
    finally:
        database.reset_session_factory(original_url)


def _load_cli():
    from scripts import bootstrap_admin

    return bootstrap_admin


def test_create_admin_command_creates_user_and_audit_log(cli_db):
    cli = _load_cli()
    result = cli.main(
        [
            "--database-url",
            cli_db,
            "create-admin",
            "--username",
            "cli-admin",
            "--password",
            "ultra-secret",
        ]
    )
    assert result == 0

    with database.SessionLocal() as session:
        user = session.exec(select(User).where(User.username == "cli-admin")).first()
        assert user is not None
        assert user.server_admin is True
        audit = session.exec(select(AuditLog).where(AuditLog.action == "admin_bootstrap")).first()
        assert audit is not None


def test_rotate_secrets_updates_env_file_and_logs(cli_db, tmp_path):
    cli = _load_cli()
    env_file = tmp_path / ".env"
    result = cli.main(
        [
            "--database-url",
            cli_db,
            "rotate-secrets",
            "--env-file",
            str(env_file),
        ]
    )
    assert result == 0
    assert env_file.exists()
    contents = env_file.read_text()
    assert "SESSION_SECRET=" in contents
    assert "API_BEARER=" in contents
    assert "MANIFEST_HMAC_SECRET=" in contents

    with database.SessionLocal() as session:
        audit = session.exec(select(AuditLog).where(AuditLog.action == "secrets_rotated")).first()
        assert audit is not None


def test_seed_sample_data_creates_demo_users(cli_db):
    cli = _load_cli()
    result = cli.main(
        [
            "--database-url",
            cli_db,
            "seed-sample-data",
            "--password",
            "demo-pass",
            "--prefix",
            "sample-",
        ]
    )
    assert result == 0

    with database.SessionLocal() as session:
        houses = session.exec(select(House).order_by(House.id)).all()
        assert houses
        for house in houses:
            username = normalize_username(f"sample-{house.external_id}")
            user = session.exec(select(User).where(User.username == username)).first()
            assert user is not None
            membership = session.exec(
                select(HouseMembership).where(HouseMembership.user_id == user.id)
            ).first()
            assert membership is not None
            assert membership.house_id == house.id
            assert membership.role == HouseRole.ADMIN

        audit = session.exec(select(AuditLog).where(AuditLog.action == "sample_data_seeded")).first()
        assert audit is not None
