"""bridged devices: node kind, bridge agent; chip and board optional

Revision ID: 7b1e0c2f4a91
Revises: 56c69bb1d0b4
Create Date: 2026-11-25 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7b1e0c2f4a91"
down_revision: str | None = "56c69bb1d0b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("kind", sa.String(length=16), server_default="node", nullable=False))
    op.add_column("nodes", sa.Column("bridge", sa.String(length=16), nullable=True))
    op.alter_column("nodes", "chip", existing_type=sa.String(length=16), nullable=True)
    op.alter_column("nodes", "board", existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM nodes WHERE kind = 'bridge'")
    op.alter_column("nodes", "board", existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.alter_column("nodes", "chip", existing_type=sa.String(length=16), nullable=False)
    op.drop_column("nodes", "bridge")
    op.drop_column("nodes", "kind")
