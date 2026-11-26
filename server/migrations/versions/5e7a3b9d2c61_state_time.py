"""outputs.actual_at: when the last state report arrived (solenoid countdowns)

Revision ID: 5e7a3b9d2c61
Revises: 9d2f6a4c1e08
Create Date: 2026-11-26 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e7a3b9d2c61"
down_revision: str | None = "9d2f6a4c1e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outputs", sa.Column("actual_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("outputs", "actual_at")
