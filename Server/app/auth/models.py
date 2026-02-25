"""SQLModel tables for authentication and authorization."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel

from ..config import settings


class HouseRole(str, Enum):
    ADMIN = "admin"
    GUEST = "guest"


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _timestamp_column(*, onupdate: bool = False) -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now() if onupdate else None,
    )


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(
        sa_column=Column(String(64), unique=True, index=True, nullable=False)
    )
    hashed_password: str = Field(
        sa_column=Column(String(255), nullable=False)
    )
    server_admin: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )

class House(SQLModel, table=True):
    __tablename__ = "houses"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_houses_external_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    display_name: str = Field(
        sa_column=Column(String(120), nullable=False)
    )
    external_id: str = Field(
        sa_column=Column(
            String(settings.MAX_HOUSE_ID_LENGTH),
            unique=True,
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )

class HouseMembership(SQLModel, table=True):
    __tablename__ = "house_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "house_id", name="uq_memberships_user_house"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", nullable=False)
    house_id: int = Field(foreign_key="houses.id", nullable=False)
    role: HouseRole = Field(
        sa_column=Column(SAEnum(HouseRole, name="house_role"), nullable=False)
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )

class RoomAccess(SQLModel, table=True):
    __tablename__ = "room_access"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "room_id", name="uq_roomaccess_membership_room"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    membership_id: int = Field(foreign_key="house_memberships.id", nullable=False)
    room_id: str = Field(sa_column=Column(String(120), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )

class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor_id: Optional[int] = Field(default=None, foreign_key="users.id")
    action: str = Field(sa_column=Column(String(120), nullable=False))
    summary: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())


class NodeRegistration(SQLModel, table=True):
    """Opaque node identifiers that may be claimed and provisioned later."""
    __tablename__ = "node_registrations"
    __table_args__ = (
        UniqueConstraint("download_id", name="uq_node_registrations_download_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(
        sa_column=Column(String(64), unique=True, nullable=False, index=True)
    )
    download_id: str = Field(
        sa_column=Column(String(64), unique=True, nullable=False, index=True)
    )
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    provisioning_token: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    token_issued_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )
    provisioned_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    assigned_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    assigned_user_id: Optional[int] = Field(
        default=None,
        foreign_key="users.id",
    )
    assigned_house_id: Optional[int] = Field(
        default=None,
        foreign_key="houses.id",
    )
    house_slug: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True),
    )
    room_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(120), nullable=True, index=True),
    )
    display_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    hardware_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )
    account_username: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True),
    )
    account_password_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    account_credentials_received_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class NodeCredential(SQLModel, table=True):
    __tablename__ = "node_credentials"
    __table_args__ = (
        UniqueConstraint("download_id", name="uq_node_credentials_download_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(
        sa_column=Column(String(64), unique=True, nullable=False, index=True)
    )
    house_slug: str = Field(sa_column=Column(String(64), nullable=False))
    room_id: str = Field(sa_column=Column(String(120), nullable=False))
    display_name: str = Field(sa_column=Column(String(120), nullable=False))
    download_id: str = Field(
        sa_column=Column(String(64), unique=True, nullable=False, index=True)
    )
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp_column())
    token_issued_at: datetime = Field(
        default_factory=_utcnow, sa_column=_timestamp_column(onupdate=True)
    )
    provisioned_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

__all__ = [
    "AuditLog",
    "House",
    "HouseMembership",
    "HouseRole",
    "NodeCredential",
    "NodeRegistration",
    "RoomAccess",
    "User",
]
