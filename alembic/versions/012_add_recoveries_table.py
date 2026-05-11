"""add recoveries table

Recoveries are read-only artefacts produced by restoring a snapshot from a
Repository. They attach to their source Account but are NOT Account rows
themselves — they never sync, they live on disk under
<store_path>/.offsite-restore/<account-id>-<timestamp>/, and Dovecot exposes
each one as an additional read-only namespace under the source account's
owners.

Revision ID: 012
Revises: 011
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | Sequence[str] | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recoveries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=True),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "restored_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("restore_path", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("restoring", "ready", "failed", "deleting", name="recoverystatus"),
            nullable=False,
            server_default="restoring",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["backup_destinations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recoveries_account_id", "recoveries", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_recoveries_account_id", table_name="recoveries")
    op.drop_table("recoveries")
    sa.Enum(name="recoverystatus").drop(op.get_bind(), checkfirst=False)
