"""initial sync total folders

Adds accounts.initial_sync_total_folders — the recap denominator for the
advancing folder count, filled by the same upstream STATUS pass that sets
initial_sync_total_messages (the set of folders it iterated). Nullable,
no backfill: NULL = unknown until the next STATUS pass runs.

Revision ID: 022
Revises: 021
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | Sequence[str] | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("initial_sync_total_folders", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "initial_sync_total_folders")
