"""Add restore_jobs table.

Revision ID: 007
Revises: 006
Create Date: 2026-05-06
"""

import sqlalchemy as sa

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "restore_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_account_id", sa.String(), nullable=False),
        sa.Column("target_account_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "restore_mode",
            sa.Enum("full", "folder", "selection", name="restoremode", create_constraint=True),
            nullable=False,
        ),
        sa.Column("folder_mapping", sa.String(), nullable=False, server_default="original"),
        sa.Column("skip_duplicates", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("selected_folders", sa.JSON(), nullable=True),
        sa.Column("selected_uids", sa.JSON(), nullable=True),
        sa.Column("total_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("restored_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["target_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("restore_jobs")
