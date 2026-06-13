"""staging areas

Per-user writable staging mailbox for curated restores: at most one
staging_areas row per user (TTL + quota), plus staging_messages rows for
per-message origin bookkeeping. The on-disk Maildir is the source of truth
for contents; these tables track quota, origin and expiry.

Revision ID: 019
Revises: 018
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019"
down_revision: str | Sequence[str] | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staging_areas",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "staging_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("staging_id", sa.String(), nullable=False),
        sa.Column("source_account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.Column("original_folder", sa.Text(), nullable=False),
        sa.Column("staged_filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["staging_id"], ["staging_areas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staging_messages_staging_id", "staging_messages", ["staging_id"])


def downgrade() -> None:
    op.drop_index("ix_staging_messages_staging_id", table_name="staging_messages")
    op.drop_table("staging_messages")
    op.drop_table("staging_areas")
