"""add app_credentials table

Per-user revocable access tokens. One row backs both the IMAP password the
Dovecot Lua passdb verifies and (phase 2+) the bearer token for the agent API,
so a user never hands an agent their login password.

Revision ID: 028
Revises: 027
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "028"
down_revision: str | Sequence[str] | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_credentials",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_prefix", sa.String(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_kind", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_credentials_user_id", "app_credentials", ["user_id"])
    op.create_index(
        "ix_app_credentials_token_prefix", "app_credentials", ["token_prefix"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_app_credentials_token_prefix", table_name="app_credentials")
    op.drop_index("ix_app_credentials_user_id", table_name="app_credentials")
    op.drop_table("app_credentials")
