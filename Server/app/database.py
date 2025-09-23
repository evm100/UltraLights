"""Database helpers for working with the authentication store."""
from __future__ import annotations

from typing import Any, Iterator

from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine

from .config import settings


def _ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent directory for SQLite databases when needed."""

    try:
        url = make_url(database_url)
    except Exception:
        return

    if not url.drivername.startswith("sqlite"):
        return

    database = url.database
    if not database or database in {":memory:", ""}:
        return

    path = settings.resolve_data_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)


def _build_engine(database_url: str):
    _ensure_sqlite_directory(database_url)
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, future=True)


engine = _build_engine(settings.AUTH_DB_URL)
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autocommit=False,
    autoflush=False,
)


def reset_session_factory(database_url: str | None = None) -> None:
    """Rebuild the engine/sessionmaker.

    Primarily intended for tests to isolate storage in temporary locations.
    """

    global engine, SessionLocal

    if database_url is not None:
        settings.AUTH_DB_URL = database_url
    engine = _build_engine(settings.AUTH_DB_URL)
    SessionLocal = sessionmaker(
        bind=engine,
        class_=Session,
        autocommit=False,
        autoflush=False,
    )


def get_session() -> Iterator[Session]:
    """Yield a database session suitable for FastAPI dependencies."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


__all__ = ["engine", "SessionLocal", "get_session", "reset_session_factory"]
