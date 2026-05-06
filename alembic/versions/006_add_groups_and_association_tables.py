"""Add groups table and missing association tables.

The groups, user_allowed_stores, group_members, and account_groups
tables were defined in models.py but never included in migrations.

Revision ID: 006
Revises: 005
Create Date: 2026-05-06
"""

import sqlalchemy as sa

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("sso_sync", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
    )

    op.create_table(
        "user_allowed_stores",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "store_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["mail_stores.id"]),
    )

    op.create_table(
        "group_members",
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "account_groups",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "group_id"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"]),
    )


def downgrade() -> None:
    op.drop_table("account_groups")
    op.drop_table("group_members")
    op.drop_table("user_allowed_stores")
    op.drop_table("groups")
