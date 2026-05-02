"""Add log_path, parsed_summary, mbsync_version, signal to SyncJob.

Revision ID: 004
Revises: 003
Create Date: 2026-05-02
"""

import sqlalchemy as sa

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("log_path", sa.String(), nullable=True))
    op.add_column("sync_jobs", sa.Column("parsed_summary", sa.Text(), nullable=True))
    op.add_column("sync_jobs", sa.Column("mbsync_version", sa.String(), nullable=True))
    op.add_column("sync_jobs", sa.Column("signal", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_jobs", "signal")
    op.drop_column("sync_jobs", "mbsync_version")
    op.drop_column("sync_jobs", "parsed_summary")
    op.drop_column("sync_jobs", "log_path")
