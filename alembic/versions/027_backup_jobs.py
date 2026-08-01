"""add backup_jobs table

Per-run history for off-site backups, sibling of sync_jobs. Also closes any
BackupPolicy left in "running" by a crash before this table existed — such
policies have no job row for the boot sweep to act on and would stay stuck
forever (production incident 2026-08-01: an OOMKill mid-backup left a policy
reporting "running" for 18 hours).

Revision ID: 027
Revises: 026
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "027"
down_revision: str | Sequence[str] | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The jobstatus type already exists — sync_jobs created it. create_type=False
# stops this migration from re-issuing CREATE TYPE and failing. Migrations only
# ever run against PostgreSQL; the test suite builds its schema with
# Base.metadata.create_all instead.
_JOBSTATUS = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="jobstatus",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("policy_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("status", _JOBSTATUS, nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="schedule"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_kind", sa.String(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("snapshot_id", sa.String(), nullable=True),
        sa.Column("bytes_processed", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_added", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["policy_id"], ["account_backups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_jobs_policy_id", "backup_jobs", ["policy_id"])
    op.create_index("ix_backup_jobs_account_id", "backup_jobs", ["account_id"])

    # One-shot repair: policies stranded in "running" by a crash that predates
    # this table. Without a job row the boot sweep cannot see them.
    op.execute(
        "UPDATE account_backups "
        "SET last_status = 'failed', "
        "    last_error = 'Interrupted: closed by migration 027 (no run record)' "
        "WHERE last_status = 'running'"
    )


def downgrade() -> None:
    op.drop_index("ix_backup_jobs_account_id", table_name="backup_jobs")
    op.drop_index("ix_backup_jobs_policy_id", table_name="backup_jobs")
    op.drop_table("backup_jobs")
