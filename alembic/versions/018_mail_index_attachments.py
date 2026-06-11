"""mail_index.attachments + messages.has_attachments/attachments_indexed_at

Revision ID: 018
Revises: 017
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: str | Sequence[str] | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    attachments_cols = [
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
    ]
    attachments_constraints = [
        sa.PrimaryKeyConstraint("account_id", "message_id_hash", "part_index"),
    ]
    if not is_sqlite:
        # SQLite cannot model cross-schema FKs to ATTACHed DBs; skip there (same as 014).
        attachments_constraints.append(
            sa.ForeignKeyConstraint(
                ["account_id", "message_id_hash"],
                [
                    "mail_index.messages.account_id",
                    "mail_index.messages.message_id_hash",
                ],
                ondelete="CASCADE",
            )
        )
    op.create_table(
        "attachments",
        *attachments_cols,
        *attachments_constraints,
        schema="mail_index",
    )

    op.add_column(
        "messages",
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="mail_index",
    )
    op.add_column(
        "messages",
        sa.Column("attachments_indexed_at", sa.DateTime(timezone=True), nullable=True),
        schema="mail_index",
    )

    if not is_sqlite:
        # Combined name+content search index (content_text used from Plan 3 on).
        op.execute(
            "CREATE INDEX idx_attachments_fts ON mail_index.attachments "
            "USING gin (to_tsvector('simple', coalesce(filename, '') || ' ' "
            "|| coalesce(content_text, '')))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if not is_sqlite:
        op.execute("DROP INDEX IF EXISTS mail_index.idx_attachments_fts")
    op.drop_table("attachments", schema="mail_index")
    op.drop_column("messages", "attachments_indexed_at", schema="mail_index")
    op.drop_column("messages", "has_attachments", schema="mail_index")
