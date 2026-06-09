"""mail_index schema with messages, snapshot_messages, rebuild_status

Revision ID: 014
Revises: 013
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

from alembic import op

revision: str = "014"
down_revision: str | Sequence[str] | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        # SQLite has no real schemas; tests ATTACH a :memory: DB as mail_index.
        op.execute("ATTACH DATABASE ':memory:' AS mail_index")
    else:
        op.execute("CREATE SCHEMA IF NOT EXISTS mail_index")

    to_addrs_type = sa.JSON() if is_sqlite else ARRAY(sa.Text())
    tsv_type = sa.Text() if is_sqlite else TSVECTOR()
    now_default = sa.text("CURRENT_TIMESTAMP") if is_sqlite else sa.text("now()")
    first_seen_default = now_default
    last_seen_default = now_default

    messages_cols = [
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("date_sent", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=True),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("to_addrs", to_addrs_type, nullable=True),
        sa.Column("folder_path", sa.Text(), nullable=False),
        sa.Column("maildir_filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=first_seen_default,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=last_seen_default,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tsv", tsv_type, nullable=True),
    ]
    messages_constraints = [
        sa.PrimaryKeyConstraint("account_id", "message_id_hash"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    ]
    op.create_table(
        "messages",
        *messages_cols,
        *messages_constraints,
        schema="mail_index",
    )
    op.create_index(
        "idx_messages_account_date",
        "messages",
        ["account_id", sa.text("date_sent DESC")],
        schema="mail_index",
    )
    if not is_sqlite:
        op.create_index(
            "idx_messages_tsv",
            "messages",
            ["tsv"],
            postgresql_using="gin",
            schema="mail_index",
        )
        op.create_index(
            "idx_messages_account_alive",
            "messages",
            ["account_id"],
            postgresql_where=sa.text("deleted_at IS NULL"),
            schema="mail_index",
        )

        # Trigger that recomputes tsv from subject + from_addr + from_name + to_addrs.
        op.execute("""
            CREATE FUNCTION mail_index.messages_tsv_trigger() RETURNS trigger AS $$
            BEGIN
                NEW.tsv := to_tsvector('simple',
                    coalesce(NEW.subject, '') || ' ' ||
                    coalesce(NEW.from_addr, '') || ' ' ||
                    coalesce(NEW.from_name, '') || ' ' ||
                    coalesce(array_to_string(NEW.to_addrs, ' '), '')
                );
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER messages_tsv_update
            BEFORE INSERT OR UPDATE ON mail_index.messages
            FOR EACH ROW EXECUTE FUNCTION mail_index.messages_tsv_trigger();
        """)

    snapshot_messages_cols = [
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
    ]
    snapshot_messages_constraints = [
        sa.PrimaryKeyConstraint("snapshot_id", "account_id", "message_id_hash"),
    ]
    if not is_sqlite:
        # SQLite cannot model cross-schema FKs to ATTACHed DBs; skip there.
        snapshot_messages_constraints.append(
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
        "snapshot_messages",
        *snapshot_messages_cols,
        *snapshot_messages_constraints,
        schema="mail_index",
    )
    op.create_index(
        "idx_snapmsg_account_msg",
        "snapshot_messages",
        ["account_id", "message_id_hash"],
        schema="mail_index",
    )

    rebuild_status_cols = [
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_progress", sa.Integer(), nullable=True),
        sa.Column("backfill_total", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    ]
    rebuild_status_constraints = [
        sa.PrimaryKeyConstraint("account_id"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    ]
    op.create_table(
        "rebuild_status",
        *rebuild_status_cols,
        *rebuild_status_constraints,
        schema="mail_index",
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_table("rebuild_status", schema="mail_index")
    op.drop_table("snapshot_messages", schema="mail_index")
    if not is_sqlite:
        op.execute("DROP TRIGGER IF EXISTS messages_tsv_update ON mail_index.messages")
        op.execute("DROP FUNCTION IF EXISTS mail_index.messages_tsv_trigger()")
        op.drop_index("idx_messages_account_alive", table_name="messages", schema="mail_index")
        op.drop_index("idx_messages_tsv", table_name="messages", schema="mail_index")
    op.drop_index("idx_messages_account_date", table_name="messages", schema="mail_index")
    op.drop_table("messages", schema="mail_index")
    if is_sqlite:
        op.execute("DETACH DATABASE mail_index")
    else:
        op.execute("DROP SCHEMA IF EXISTS mail_index")
