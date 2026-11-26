"""scenes and scene runs (undo)

Revision ID: b4c1d7e2f903
Revises: 5e7a3b9d2c61
Create Date: 2026-11-26 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4c1d7e2f903"
down_revision: str | None = "5e7a3b9d2c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("home_id", sa.Integer(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "scene_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scene_id", sa.Integer(), sa.ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("home_id", sa.Integer(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("src", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("previous", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scene_runs")
    op.drop_table("scenes")
