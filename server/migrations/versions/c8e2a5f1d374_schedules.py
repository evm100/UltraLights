"""schedules; home time zone and location

Revision ID: c8e2a5f1d374
Revises: b4c1d7e2f903
Create Date: 2026-11-26 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8e2a5f1d374"
down_revision: str | None = "b4c1d7e2f903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("homes", sa.Column("timezone", sa.String(length=64), nullable=True))
    op.add_column("homes", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("homes", sa.Column("longitude", sa.Float(), nullable=True))
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("home_id", sa.Integer(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_column("homes", "longitude")
    op.drop_column("homes", "latitude")
    op.drop_column("homes", "timezone")
