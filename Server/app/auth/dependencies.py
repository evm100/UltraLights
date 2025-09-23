"""FastAPI dependencies for authentication."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from ..database import get_session
from .models import User
from .security import SESSION_COOKIE_NAME, SessionTokenData, verify_session_token


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """Return the authenticated ``User`` or raise ``401``."""

    token_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not token_value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    token_data: SessionTokenData | None = verify_session_token(token_value)
    if token_data is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    user = session.exec(select(User).where(User.id == token_data.user_id)).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    request.state.user = user
    request.state.session_token = token_data
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Ensure the current user has administrator privileges."""

    if not current_user.server_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return current_user


__all__ = ["get_current_user", "require_admin"]

