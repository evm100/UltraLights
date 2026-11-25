"""home roles: admin becomes an account flag, owner/member/guest live only on home_members

Revision ID: 3c8d5e1a9b27
Revises: 7b1e0c2f4a91
Create Date: 2026-11-25 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c8d5e1a9b27"
down_revision: str | None = "7b1e0c2f4a91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE users SET is_admin = (role = 'admin')")
    op.drop_column("users", "role")
    op.create_check_constraint("home_members_role", "home_members", "role IN ('owner', 'member', 'guest')")


def downgrade() -> None:
    op.drop_constraint("home_members_role", "home_members", type_="check")
    op.add_column("users", sa.Column("role", sa.String(length=16), server_default="member", nullable=False))
    # The old global role is the highest home role (or admin).
    op.execute("""
        UPDATE users u SET role = CASE
          WHEN u.is_admin THEN 'admin'
          WHEN EXISTS (SELECT 1 FROM home_members m WHERE m.user_id = u.id AND m.role = 'owner') THEN 'owner'
          WHEN EXISTS (SELECT 1 FROM home_members m WHERE m.user_id = u.id AND m.role = 'member') THEN 'member'
          ELSE 'guest' END""")
    op.alter_column("users", "role", server_default=None)
    op.drop_column("users", "is_admin")
