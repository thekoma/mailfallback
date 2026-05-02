"""Add Account.migrating flag.

Revision ID: 002
Revises: 001
Create Date: 2026-04-30
"""

import sqlalchemy as sa

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("migrating", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("accounts", "migrating")
