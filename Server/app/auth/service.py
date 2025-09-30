"""Service helpers for authentication storage."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, select

from .. import node_credentials, registry
from ..config import settings
from .. import database
from .models import AuditLog, House, User
from .security import hash_password, normalize_username


def init_auth_storage() -> None:
    """Ensure tables exist and seed initial data."""

    SQLModel.metadata.create_all(database.engine)
    _ensure_node_registration_columns()

    with database.SessionLocal() as session:
        _seed_initial_admin(session)
        _sync_registry_houses(session)
        node_credentials.migrate_credentials_to_registrations(session)
        _sync_registry_nodes(session)


def _seed_initial_admin(session: Session) -> None:
    """Create the initial server admin when the table is empty."""

    existing_admin = session.exec(
        select(User).where(User.server_admin.is_(True))
    ).first()
    if existing_admin:
        return

    username = normalize_username(settings.INITIAL_ADMIN_USERNAME)
    password = settings.INITIAL_ADMIN_PASSWORD

    if not username or not password:
        return

    hashed = hash_password(password)
    user = User(username=username, hashed_password=hashed, server_admin=True)
    session.add(user)
    session.commit()


def _sync_registry_houses(session: Session) -> None:
    """Ensure ``House`` rows exist for each registry entry."""

    existing = {
        external_id
        for external_id in session.exec(select(House.external_id))
        if isinstance(external_id, str)
    }

    for entry in settings.DEVICE_REGISTRY:
        external_id = registry.get_house_external_id(entry)
        if external_id in existing:
            continue
        display_name = str(entry.get("name") or entry.get("id") or external_id)
        session.add(House(display_name=display_name, external_id=external_id))

    session.commit()


def _sync_registry_nodes(session: Session) -> None:
    """Ensure credential rows exist for every registry node."""

    node_credentials.sync_registry_nodes(session)


def _ensure_node_registration_columns() -> None:
    """Backfill newly added ``node_registrations`` columns if missing."""

    inspector = inspect(database.engine)
    required: Dict[str, Dict[str, str]] = {
        "node_registrations": {
            "account_username": "ALTER TABLE node_registrations ADD COLUMN account_username VARCHAR(64)",
            "account_password_hash": "ALTER TABLE node_registrations ADD COLUMN account_password_hash VARCHAR(255)",
            "account_credentials_received_at": "ALTER TABLE node_registrations ADD COLUMN account_credentials_received_at TIMESTAMP",
            "certificate_fingerprint": "ALTER TABLE node_registrations ADD COLUMN certificate_fingerprint VARCHAR(128)",
            "certificate_pem_path": "ALTER TABLE node_registrations ADD COLUMN certificate_pem_path VARCHAR(255)",
            "private_key_pem_path": "ALTER TABLE node_registrations ADD COLUMN private_key_pem_path VARCHAR(255)",
            "certificate_bundle_path": "ALTER TABLE node_registrations ADD COLUMN certificate_bundle_path VARCHAR(255)",
        },
        "node_credentials": {
            "certificate_fingerprint": "ALTER TABLE node_credentials ADD COLUMN certificate_fingerprint VARCHAR(128)",
            "certificate_pem_path": "ALTER TABLE node_credentials ADD COLUMN certificate_pem_path VARCHAR(255)",
            "private_key_pem_path": "ALTER TABLE node_credentials ADD COLUMN private_key_pem_path VARCHAR(255)",
            "certificate_bundle_path": "ALTER TABLE node_credentials ADD COLUMN certificate_bundle_path VARCHAR(255)",
        },
    }

    statements: List[str] = []
    for table_name, column_statements in required.items():
        try:
            existing = {
                column_info["name"]
                for column_info in inspector.get_columns(table_name)
            }
        except Exception:  # pragma: no cover - table may not exist yet
            continue
        for column_name, statement in column_statements.items():
            if column_name not in existing:
                statements.append(statement)

    if not statements:
        return

    with database.engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def create_user(
    session: Session,
    username: str,
    password: str,
    *,
    server_admin: bool = False,
) -> User:
    """Create a new ``User`` and return it."""

    normalized_username = normalize_username(username)
    if not normalized_username:
        raise ValueError("username cannot be empty")

    user = User(
        username=normalized_username,
        hashed_password=hash_password(password),
        server_admin=server_admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def record_audit_event(
    session: Session,
    *,
    actor: Optional[User],
    action: str,
    summary: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    commit: bool = False,
) -> AuditLog:
    """Persist an :class:`AuditLog` entry."""

    entry = AuditLog(
        actor_id=actor.id if actor and actor.id is not None else None,
        action=action,
        summary=summary,
        data=data or {},
    )
    session.add(entry)
    session.flush()
    if commit:
        session.commit()
        session.refresh(entry)
    return entry


__all__ = ["create_user", "init_auth_storage", "record_audit_event"]
