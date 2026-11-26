"""OTA updates; firmware packages index their app image by sha256

Revision ID: f1b4d8e6a273
Revises: e3a7c9f2b158
Create Date: 2026-11-26 23:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1b4d8e6a273"
down_revision: str | None = "e3a7c9f2b158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("firmware_packages", sa.Column("app_sha256", sa.String(64), nullable=True))
    op.execute("""UPDATE firmware_packages p SET app_sha256 = (
                    SELECT f->>'sha256' FROM jsonb_array_elements(p.manifest->'files') f
                    WHERE f->>'name' = p.manifest->>'app' LIMIT 1)""")
    op.create_index("ix_firmware_packages_app_sha256", "firmware_packages", ["app_sha256"])
    op.create_table(
        "ota_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("firmware_packages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("from_version", sa.String(32), nullable=True),
        sa.Column("to_version", sa.String(32), nullable=False),
        sa.Column("cmd_id", sa.String(40), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="sent"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("src", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('sent', 'downloading', 'restarting', 'done', 'failed')", name="ota_updates_status"),
    )
    op.create_index("ix_ota_updates_node_id", "ota_updates", ["node_id"])
    op.create_index("ix_ota_updates_status", "ota_updates", ["status"])


def downgrade() -> None:
    op.drop_table("ota_updates")
    op.drop_index("ix_firmware_packages_app_sha256", "firmware_packages")
    op.drop_column("firmware_packages", "app_sha256")
