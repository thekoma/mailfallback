"""add backup progress columns to account_backups

Revision ID: 011
Revises: 010
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | Sequence[str] | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_backups",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_backups",
        sa.Column("last_successful_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_backups",
        sa.Column(
            "last_snapshot_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "account_backups",
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("account_backups", "last_snapshot_at")
    op.drop_column("account_backups", "last_snapshot_count")
    op.drop_column("account_backups", "last_successful_run_at")
    op.drop_column("account_backups", "last_run_at")
