from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("admin", "owner", "member", "guest")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # admin | owner | member | guest
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    csrf: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(lazy="joined")


class Home(Base):
    __tablename__ = "homes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()


class HomeMember(Base):
    __tablename__ = "home_members"
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | member | guest


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(9), unique=True)  # ul-xxxxxx
    name: Mapped[str] = mapped_column(String(64))
    chip: Mapped[str] = mapped_column(String(16))
    home_id: Mapped[int | None] = mapped_column(ForeignKey("homes.id", ondelete="SET NULL"), index=True)
    board: Mapped[dict[str, Any]] = mapped_column(JSONB)
    board_crc: Mapped[str | None] = mapped_column(String(8))  # crc of the last provisioned payload
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contracts config (v, rev, outputs)
    config_rev: Mapped[int] = mapped_column(Integer, default=0)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    health: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Output(Base):
    __tablename__ = "outputs"
    __table_args__ = (UniqueConstraint("node_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64), default="")
    caps: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    desired: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # last retained set payload
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # last state payload
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # cct_range, pixels, ... from info


class EnrollToken(Base):
    __tablename__ = "enroll_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_pubkey_sha256: Mapped[str | None] = mapped_column(String(64))
    cert_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id", ondelete="SET NULL"))


class Certificate(Base):
    __tablename__ = "certificates"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(40), unique=True)  # hex
    cn: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"), index=True)
    pem: Mapped[str] = mapped_column(Text)
    pubkey_sha256: Mapped[str] = mapped_column(String(64))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))


class FirmwarePackage(Base):
    __tablename__ = "firmware_packages"
    __table_args__ = (UniqueConstraint("profile", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    profile: Mapped[str] = mapped_column(String(32))
    chip: Mapped[str] = mapped_column(String(16))
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = _now()


class BoardTemplate(Base):
    """Saved board descriptors the builder can start from (e.g. a PCB revision)."""
    __tablename__ = "board_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(48), unique=True)
    board: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = _now()
    src: Mapped[str] = mapped_column(String(64))  # same format as contract src
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
