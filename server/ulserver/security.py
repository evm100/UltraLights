"""Passwords (argon2), cookie sessions and CSRF.

The session cookie is HttpOnly, SameSite=Lax and Secure in production. Every
state-changing request must also carry the X-UL-CSRF header equal to the session's
CSRF value (handed to the SPA by /api/auth/me and the login response), so a
cross-site form can't act for a signed-in user.
"""
import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .access import Access, load_access
from .config import get_settings
from .db import get_db
from .models import Session, User

COOKIE = "ul_session"
CSRF_HEADER = "x-ul-csrf"
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    try:
        return _ph.verify(hash_, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


_DUMMY = hash_password("timing-equaliser")


def verify_or_dummy(user: User | None, pw: str) -> bool:
    """Spend the same time whether or not the user exists."""
    if user is None or not user.active:
        verify_password(_DUMMY, pw)
        return False
    return verify_password(user.password_hash, pw)


def sha256(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


async def create_session(db: AsyncSession, user: User) -> tuple[str, Session]:
    token = secrets.token_urlsafe(32)
    s = Session(
        token_hash=sha256(token),
        csrf=secrets.token_urlsafe(24),
        user_id=user.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=get_settings().session_days),
    )
    db.add(s)
    await db.flush()
    return token, s


async def end_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(Session).where(Session.token_hash == sha256(token)))


@dataclass
class Auth:
    user: User
    session: Session
    access: Access


async def _lookup(db: AsyncSession, token: str | None) -> Auth | None:
    if not token:
        return None
    s = (await db.execute(select(Session).where(Session.token_hash == sha256(token)))).scalar_one_or_none()
    if s is None or s.expires_at < dt.datetime.now(dt.timezone.utc) or not s.user.active:
        return None
    return Auth(s.user, s, await load_access(db, s.user))


async def optional_auth(request: Request, db: AsyncSession = Depends(get_db)) -> Auth | None:
    return await _lookup(db, request.cookies.get(COOKIE))


async def require_auth(request: Request, db: AsyncSession = Depends(get_db)) -> Auth:
    auth = await _lookup(db, request.cookies.get(COOKIE))
    if auth is None:
        raise HTTPException(401, "sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, auth.session.csrf):
            raise HTTPException(403, "missing or wrong CSRF header")
    return auth


async def require_admin(auth: Auth = Depends(require_auth)) -> Auth:
    if not auth.user.is_admin:
        raise HTTPException(403, "admin only")
    return auth


async def ws_auth(ws: WebSocket, db: AsyncSession) -> Auth | None:
    """WebSockets can't carry custom headers from browsers; the cookie plus an Origin
    check stands in for CSRF."""
    auth = await _lookup(db, ws.cookies.get(COOKIE))
    if auth is None:
        return None
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if origin and host and origin.split("://", 1)[-1] != host:
        return None
    return auth


def src_for(auth: Auth, prefix: str = "ui") -> str:
    return f"{prefix}:{auth.user.id}"
