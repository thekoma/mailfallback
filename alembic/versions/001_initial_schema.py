"""Initial schema — UUID maildir with Account.store_id.

Revision ID: 001
Revises:
Create Date: 2026-04-30
"""

import sqlalchemy as sa

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_stores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column(
            "role",
            sa.Enum("admin", "user", name="userrole", create_constraint=True),
            nullable=False,
        ),
        sa.Column("oidc_subject", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("migrating", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("oidc_subject"),
        sa.ForeignKeyConstraint(["store_id"], ["mail_stores.id"]),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email_address", sa.String(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(), nullable=False, server_default="other"),
        sa.Column("imap_host", sa.String(), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column(
            "auth_type",
            sa.Enum("oauth2", "app_password", name="authtype", create_constraint=True),
            nullable=False,
        ),
        sa.Column("credentials", sa.Text(), nullable=True),
        sa.Column("imap_user", sa.String(), nullable=True),
        sa.Column("tls_type", sa.String(), nullable=False, server_default="IMAPS"),
        sa.Column("maildir_path", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("sync_schedule", sa.String(), nullable=True, server_default="*/10 * * * *"),
        sa.Column("extra_config", sa.Text(), nullable=True),
        sa.Column(
            "sync_state",
            sa.Enum("idle", "syncing", "error", name="syncstate", create_constraint=True),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("total_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unread_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("maildir_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("folder_stats", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("maildir_path"),
        sa.ForeignKeyConstraint(["store_id"], ["mail_stores.id"]),
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                name="jobstatus",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False, server_default="api"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
    )
    op.create_index(op.f("ix_sync_jobs_account_id"), "sync_jobs", ["account_id"])

    op.create_table(
        "account_owners",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "user_id"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "store_migrations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("source_store_id", sa.String(), nullable=False),
        sa.Column("target_store_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "copying",
                "verifying",
                "cleaning",
                "completed",
                "failed",
                name="migrationstatus",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("copied_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("copied_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_store_id"], ["mail_stores.id"]),
        sa.ForeignKeyConstraint(["target_store_id"], ["mail_stores.id"]),
    )


def downgrade() -> None:
    op.drop_table("store_migrations")
    op.drop_table("account_owners")
    op.drop_index(op.f("ix_sync_jobs_account_id"), table_name="sync_jobs")
    op.drop_table("sync_jobs")
    op.drop_table("accounts")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
    op.drop_table("mail_stores")
