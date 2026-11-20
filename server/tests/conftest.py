"""Test setup: a dedicated database (ul_test on the dev PostgreSQL), a throwaway PKI and data dir.

Run via server/tools/dev.sh test (starts the dev PostgreSQL) or set UL_TEST_DATABASE_URL.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="ul-test-"))
os.environ.pop("UL_ENV_FILE", None)
os.environ["UL_DATABASE_URL"] = os.environ.get(
    "UL_TEST_DATABASE_URL", "postgresql+psycopg://ultralights@127.0.0.1:5433/ul_test")
os.environ["UL_PKI_DIR"] = str(TMP / "pki")
os.environ["UL_DATA_DIR"] = str(TMP / "data")
os.environ["UL_PUBLIC_HOST"] = "lights.test"
os.environ["UL_BRIDGE_ENABLED"] = "false"
os.environ["UL_COOKIE_SECURE"] = "false"
os.environ["UL_ENROLL_RATE_PER_MIN"] = "1000"

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
CONTRACTS = REPO / "contracts"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def migrated_db():
    """Fresh schema via the real migrations (so migrations are tested too)."""
    from sqlalchemy import create_engine, text

    from ulserver.cli import _db_upgrade
    from ulserver.config import get_settings

    eng = create_engine(get_settings().database_url)
    try:
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test database not reachable ({e.__class__.__name__}); run server/tools/dev.sh test")
    eng.dispose()
    _db_upgrade()
    return True


@pytest.fixture(scope="session")
def pki(migrated_db):
    from ulserver.config import get_settings
    from ulserver.pki import DevicePKI

    p = DevicePKI(get_settings().pki_dir)
    p.init()
    p.ensure_bridge_cert()
    return p


@pytest.fixture()
def client(pki):
    from fastapi.testclient import TestClient

    from ulserver.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c


@pytest.fixture(scope="session")
def admin_creds(migrated_db):
    import asyncio

    from ulserver.cli import _create_admin

    asyncio.run(_create_admin("admin", "correct horse battery"))
    return ("admin", "correct horse battery")


@pytest.fixture()
def admin(client, admin_creds):
    """A signed-in admin TestClient with the CSRF header set."""
    r = client.post("/api/auth/login", json={"username": admin_creds[0], "password": admin_creds[1]})
    assert r.status_code == 200, r.text
    client.headers["X-UL-CSRF"] = r.json()["csrf"]
    return client
