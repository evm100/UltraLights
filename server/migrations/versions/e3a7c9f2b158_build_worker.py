"""workers and build jobs

Revision ID: e3a7c9f2b158
Revises: d5f9b2c7e416
Create Date: 2026-11-26 21:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3a7c9f2b158"
down_revision: str | None = "d5f9b2c7e416"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(48), nullable=False, unique=True),
        sa.Column("kinds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ip", sa.String(64), nullable=True),
    )
    op.create_table(
        "build_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("replace", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("commit", sa.String(48), nullable=True),
        sa.Column("version", sa.String(32), nullable=True),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("firmware_packages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="build_jobs_status"),
    )
    op.create_index("ix_build_jobs_status", "build_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_build_jobs_status", "build_jobs")
    op.drop_table("build_jobs")
    op.drop_table("workers")
