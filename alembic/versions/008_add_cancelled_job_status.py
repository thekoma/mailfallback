"""Add cancelled value to jobstatus enum.

Revision ID: 008
Revises: 007
Create Date: 2026-05-08
"""

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    pass
