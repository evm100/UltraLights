"""Security helpers for authentication and session management."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
from sqlmodel import Session, select

from ..config import settings
from .models import User


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Cookies ------------------------------------------------------------------
SESSION_COOKIE_NAME = "ultralights_session"
SESSION_TOKEN_TTL = timedelta(hours=12)
SESSION_TOKEN_TTL_SECONDS = int(SESSION_TOKEN_TTL.total_seconds())
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_SAMESITE = "lax"


@dataclass
class SessionTokenData:
    """Information extracted from a verified session token."""

    user_id: int
    username: Optional[str]
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


def hash_password(password: str) -> str:
    """Hash ``password`` using bcrypt."""

    if not isinstance(password, str):
        raise TypeError("password must be a string")
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Return ``True`` if ``password`` matches ``hashed_password``."""

    if not password or not hashed_password:
        return False
    try:
        return _pwd_context.verify(password, hashed_password)
    except ValueError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Return ``True`` if ``hashed_password`` should be upgraded."""

    if not hashed_password:
        return True
    return _pwd_context.needs_update(hashed_password)


def authenticate_user(session: Session, username: str, password: str) -> Optional[User]:
    """Return the ``User`` that matches ``username``/``password`` or ``None``."""

    if not username or not password:
        return None
    statement = select(User).where(User.username == username)
    user = session.exec(statement).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_session_token(
    user: User,
    *,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed token identifying ``user`` with an expiry timestamp."""

    if user.id is None:
        raise ValueError("user must be persisted before creating a session token")
    lifetime = expires_delta or SESSION_TOKEN_TTL
    expires_at = datetime.now(timezone.utc) + lifetime
    payload = {
        "sub": int(user.id),
        "usr": user.username,
        "exp": int(expires_at.timestamp()),
        "nonce": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(_secret_key(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"


def verify_session_token(token: str) -> Optional[SessionTokenData]:
    """Validate ``token`` and return the decoded data when successful."""

    if not token or "." not in token:
        return None
    try:
        payload_b64, signature_b64 = token.split(".", 1)
        payload_bytes = _b64decode(payload_b64)
        signature = _b64decode(signature_b64)
    except (ValueError, binascii.Error):
        return None

    expected_signature = hmac.new(
        _secret_key(), payload_bytes, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    user_id = _coerce_int(payload.get("sub"))
    expires_ts = _coerce_int(payload.get("exp"))
    username = payload.get("usr")
    if user_id is None or expires_ts is None:
        return None

    expires_at = datetime.fromtimestamp(expires_ts, tz=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return None

    clean_username = str(username) if username is not None else None
    return SessionTokenData(user_id=user_id, username=clean_username, expires_at=expires_at)


def set_session_cookie(response, token: str) -> None:
    """Attach the session ``token`` to ``response`` as a secure cookie."""

    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TOKEN_TTL_SECONDS,
        expires=SESSION_TOKEN_TTL_SECONDS,
        path=SESSION_COOKIE_PATH,
        httponly=True,
        secure=_session_cookie_secure(),
        samesite=SESSION_COOKIE_SAMESITE,
    )


def clear_session_cookie(response) -> None:
    """Expire the session cookie on ``response``."""

    response.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        path=SESSION_COOKIE_PATH,
        httponly=True,
        secure=_session_cookie_secure(),
        samesite=SESSION_COOKIE_SAMESITE,
    )


def _secret_key() -> bytes:
    secret = settings.SESSION_SECRET
    if not secret:
        raise RuntimeError("SESSION_SECRET must be configured")
    return secret.encode("utf-8")


def _session_cookie_secure() -> bool:
    return settings.PUBLIC_BASE.startswith("https://")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _coerce_int(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = [
    "SESSION_COOKIE_NAME",
    "SESSION_TOKEN_TTL",
    "SESSION_TOKEN_TTL_SECONDS",
    "SessionTokenData",
    "authenticate_user",
    "clear_session_cookie",
    "create_session_token",
    "hash_password",
    "needs_rehash",
    "set_session_cookie",
    "verify_password",
    "verify_session_token",
]

