from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # home roles live on HomeMember
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
    timezone: Mapped[str | None] = mapped_column(String(64))  # IANA name; schedules need it
    latitude: Mapped[float | None] = mapped_column(Float)  # only for sunrise / sunset schedules
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _now()


class HomeMember(Base):
    __tablename__ = "home_members"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member', 'guest')", name="home_members_role"),)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | member | guest (access.HOME_ROLES)


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
    kind: Mapped[str] = mapped_column(String(16), default="node", server_default="node")  # node | bridge
    chip: Mapped[str | None] = mapped_column(String(16))  # None for bridges
    bridge: Mapped[str | None] = mapped_column(String(16))  # bridges only: which agent (openrgb)
    home_id: Mapped[int | None] = mapped_column(ForeignKey("homes.id", ondelete="SET NULL"), index=True)
    board: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # None for bridges
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
    actual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when it arrived
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
    app_sha256: Mapped[str | None] = mapped_column(String(64), index=True)  # OTA image; devices download it by this
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


class GuestGrant(Base):
    """What a guest may use in a home (contracts/schemas/grant). Goes with the membership."""
    __tablename__ = "guest_grants"
    __table_args__ = (ForeignKeyConstraint(["home_id", "user_id"], ["home_members.home_id", "home_members.user_id"],
                                           ondelete="CASCADE"),)
    home_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = _now()


class Invite(Base):
    """Single-use link that adds a person to a home with a role. A guest invite carries the
    grant the guest gets."""
    __tablename__ = "invites"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member', 'guest')", name="invites_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    grant: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Scene(Base):
    """A saved look (contracts/schemas/scene). doc holds the contract document."""
    __tablename__ = "scenes"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(48))
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class SceneRun(Base):
    """One application of a scene, with what the outputs were set to before, for undo."""
    __tablename__ = "scene_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    src: Mapped[str] = mapped_column(String(64))  # who applied it (ui:<user>, sched:<id>, ai:<job>)
    at: Mapped[datetime] = _now()
    previous: Mapped[list[Any]] = mapped_column(JSONB)  # [{dev, out, state}] desired before the run
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Schedule(Base):
    """When something happens (contracts/schemas/schedule). doc holds the contract document;
    last_start / last_until are the event times already fired, so a restart never repeats one."""
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(48))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    last_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Worker(Base):
    """A program on another machine (the workstation) that polls for jobs over HTTPS.
    It authenticates with a bearer token; only its sha256 is stored."""
    __tablename__ = "workers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(48), unique=True)
    kinds: Mapped[list[Any]] = mapped_column(JSONB)  # job kinds it takes, e.g. ["build"]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))


class BuildJob(Base):
    """One firmware build: profile at a git ref, claimed and run by a build worker."""
    __tablename__ = "build_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
                                      name="build_jobs_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    profile: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(100))
    replace: Mapped[bool] = mapped_column(Boolean, default=False)  # may overwrite an existing profile@version
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"))
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(String(500))
    commit: Mapped[str | None] = mapped_column(String(48))
    version: Mapped[str | None] = mapped_column(String(32))
    package_id: Mapped[int | None] = mapped_column(ForeignKey("firmware_packages.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OtaUpdate(Base):
    """One firmware update pushed to one node, followed from the cmd to the version the node
    comes back with: sent -> downloading -> restarting -> done, or failed."""
    __tablename__ = "ota_updates"
    __table_args__ = (CheckConstraint("status IN ('sent', 'downloading', 'restarting', 'done', 'failed')",
                                      name="ota_updates_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[int | None] = mapped_column(ForeignKey("firmware_packages.id", ondelete="SET NULL"))
    profile: Mapped[str] = mapped_column(String(32))
    from_version: Mapped[str | None] = mapped_column(String(32))
    to_version: Mapped[str] = mapped_column(String(32))
    cmd_id: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="sent", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200))
    src: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
