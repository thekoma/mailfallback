"""add backup_destinations and account_backups tables

Revision ID: 010
Revises: 009
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | Sequence[str] | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_destinations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "backend_type",
            sa.Enum("s3", "local", name="backendtype"),
            nullable=False,
        ),
        sa.Column("s3_endpoint", sa.String(), nullable=True),
        sa.Column("s3_bucket", sa.String(), nullable=True),
        sa.Column("s3_access_key", sa.String(), nullable=True),
        sa.Column("s3_secret_key", sa.String(), nullable=True),
        sa.Column("local_path", sa.String(), nullable=True),
        sa.Column("restic_password", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "account_backups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("destination_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column(
            "retention_preset",
            sa.Enum("light", "standard", "full", "custom", name="retentionpreset"),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("keep_daily", sa.Integer(), nullable=True),
        sa.Column("keep_weekly", sa.Integer(), nullable=True),
        sa.Column("keep_monthly", sa.Integer(), nullable=True),
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_status",
            sa.Enum("idle", "running", "completed", "failed", name="backupstatus"),
            nullable=False,
            server_default="idle",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["backup_destinations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_backups_account_id", "account_backups", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_account_backups_account_id", table_name="account_backups")
    op.drop_table("account_backups")
    op.drop_table("backup_destinations")
    sa.Enum(name="backupstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="retentionpreset").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="backendtype").drop(op.get_bind(), checkfirst=True)
