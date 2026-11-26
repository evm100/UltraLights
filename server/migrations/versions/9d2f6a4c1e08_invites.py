"""invites: single-use links that add a person to a home as a member or co-owner

Revision ID: 9d2f6a4c1e08
Revises: 3c8d5e1a9b27
Create Date: 2026-11-25 20:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d2f6a4c1e08"
down_revision: str | None = "3c8d5e1a9b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False, unique=True),
        sa.Column("home_id", sa.Integer(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'member')", name="invites_role"),
    )


def downgrade() -> None:
    op.drop_table("invites")
