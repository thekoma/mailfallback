"""repository attachments + config backup columns

- repository_attachments: orphan restic prefixes attached to accounts as
  read-only restore sources (unique per repository+prefix).
- backup_destinations: config_backup_enabled/passphrase + last run status,
  for the encrypted MFB configuration backup feature.

Revision ID: 015
Revises: 014
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | Sequence[str] | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_attachments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repository_id"], ["backup_destinations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "prefix", name="uq_repo_attachment_prefix"),
    )
    op.create_index(
        "ix_repository_attachments_repository_id",
        "repository_attachments",
        ["repository_id"],
    )
    op.create_index(
        "ix_repository_attachments_account_id",
        "repository_attachments",
        ["account_id"],
    )

    op.add_column(
        "backup_destinations",
        sa.Column("config_backup_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "backup_destinations", sa.Column("config_backup_passphrase", sa.String(), nullable=True)
    )
    op.add_column(
        "backup_destinations",
        sa.Column("last_config_backup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_destinations",
        sa.Column("last_config_backup_status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backup_destinations", "last_config_backup_status")
    op.drop_column("backup_destinations", "last_config_backup_at")
    op.drop_column("backup_destinations", "config_backup_passphrase")
    op.drop_column("backup_destinations", "config_backup_enabled")
    op.drop_index("ix_repository_attachments_account_id", table_name="repository_attachments")
    op.drop_index("ix_repository_attachments_repository_id", table_name="repository_attachments")
    op.drop_table("repository_attachments")
