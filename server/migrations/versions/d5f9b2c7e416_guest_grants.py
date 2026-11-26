"""guest grants; guest invites carry a grant

Revision ID: d5f9b2c7e416
Revises: c8e2a5f1d374
Create Date: 2026-11-26 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f9b2c7e416"
down_revision: str | None = "c8e2a5f1d374"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_grants",
        sa.Column("home_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["home_id", "user_id"], ["home_members.home_id", "home_members.user_id"], ondelete="CASCADE"),
    )
    op.add_column("invites", sa.Column("grant", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_constraint("invites_role", "invites", type_="check")
    op.create_check_constraint("invites_role", "invites", "role IN ('owner', 'member', 'guest')")


def downgrade() -> None:
    op.execute("DELETE FROM invites WHERE role = 'guest'")
    op.drop_constraint("invites_role", "invites", type_="check")
    op.create_check_constraint("invites_role", "invites", "role IN ('owner', 'member')")
    op.drop_column("invites", "grant")
    op.drop_table("guest_grants")
