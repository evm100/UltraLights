from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database as database_module
from app.auth.models import House, User
from app.auth.security import SESSION_COOKIE_NAME, verify_session_token
from app.auth.service import create_user
from app.config import settings


class _NoopBus:
    def __getattr__(self, name: str):  # pragma: no cover - simple stub
        def _noop(*args, **kwargs):
            return None

        return _noop


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    original_url = settings.AUTH_DB_URL
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database_module.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "PUBLIC_BASE", "https://testserver")
    monkeypatch.setattr(settings, "LOGIN_ATTEMPT_LIMIT", 2, raising=False)
    monkeypatch.setattr(settings, "LOGIN_ATTEMPT_WINDOW", 60, raising=False)
    monkeypatch.setattr(settings, "LOGIN_BACKOFF_SECONDS", 1, raising=False)

    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())

    import app.motion
    import app.status_monitor

    monkeypatch.setattr(app.motion.motion_manager, "start", lambda: None)
    monkeypatch.setattr(app.motion.motion_manager, "stop", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "start", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "stop", lambda: None)

    from app.auth.throttling import reset_login_rate_limiter

    reset_login_rate_limiter()

    from app.main import app as fastapi_app

    try:
        with TestClient(fastapi_app, base_url="https://testserver") as client:
            yield client
    finally:
        database_module.reset_session_factory(original_url)


def _create_user(username: str, password: str, *, admin: bool = False) -> tuple[User, str]:
    with database_module.SessionLocal() as session:
        user = create_user(session, username, password, server_admin=admin)
        house_external_id = session.exec(select(House.external_id).order_by(House.id)).first()
        assert house_external_id, "house registry should seed at least one entry"
        return user, str(house_external_id)


def test_login_success_sets_cookie_and_allows_access(client: TestClient) -> None:
    user, house_external_id = _create_user("login-user", "super-secret", admin=True)

    response = client.post(
        "/login",
        data={"username": "login-user", "password": "super-secret"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/house/{house_external_id}"

    cookie_header = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Secure" in cookie_header

    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    token_data = verify_session_token(token)
    assert token_data is not None
    assert token_data.user_id == user.id

    page = client.get(f"/house/{house_external_id}")
    assert page.status_code == 200


def test_login_rejects_invalid_credentials(client: TestClient) -> None:
    _, house_external_id = _create_user("login-user", "correct-pass")

    bad = client.post(
        "/login",
        data={"username": "login-user", "password": "wrong-pass"},
    )

    assert bad.status_code == 400
    assert "Invalid username or password" in bad.text

    cookie_header = bad.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in cookie_header
    assert "Max-Age=0" in cookie_header

    unauthenticated = client.get(
        f"/house/{house_external_id}", follow_redirects=False
    )
    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["location"] == "/login"


def test_logout_clears_cookie_and_blocks_access(client: TestClient) -> None:
    _, house_external_id = _create_user("login-user", "logmeout")

    client.post(
        "/login",
        data={"username": "login-user", "password": "logmeout"},
    )

    logout = client.get("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    cookie_header = logout.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in cookie_header
    assert "Max-Age=0" in cookie_header

    protected = client.get(
        f"/house/{house_external_id}", follow_redirects=False
    )
    assert protected.status_code == 303
    assert protected.headers["location"] == "/login"


def test_login_rate_limit_blocks_and_expires(client: TestClient) -> None:
    _create_user("login-user", "super-secret")

    first = client.post(
        "/login",
        data={"username": "login-user", "password": "wrong-pass"},
    )
    assert first.status_code == 400

    second = client.post(
        "/login",
        data={"username": "login-user", "password": "wrong-pass"},
    )
    assert second.status_code == 429
    assert "Too many login attempts" in second.text

    blocked = client.post(
        "/login",
        data={"username": "login-user", "password": "super-secret"},
        follow_redirects=False,
    )
    assert blocked.status_code == 429

    time.sleep(1.2)

    recovery = client.post(
        "/login",
        data={"username": "login-user", "password": "super-secret"},
        follow_redirects=False,
    )
    assert recovery.status_code == 303
