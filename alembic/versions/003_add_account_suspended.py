"""Add Account.suspended flag.

Revision ID: 003
Revises: 002
Create Date: 2026-04-30
"""

import sqlalchemy as sa

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("suspended", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("accounts", "suspended")
