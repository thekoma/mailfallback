"""mail_index.messages: cc_addrs, bcc_addrs, recipients_indexed_at (#255)

Until now only To was indexed, so Cc/Bcc recipients were invisible to search,
the agent API and the MCP tools. The tsv trigger now covers both new columns.

Rows that pre-date this migration keep recipients_indexed_at NULL: the next
live index walk (upsert_message_set) re-reads their headers and fills Cc/Bcc,
and that UPDATE re-fires the trigger, so their tsv picks the new columns up too.

Revision ID: 029
Revises: 028
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision: str = "029"
down_revision: str | Sequence[str] | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TSV_FUNCTION = """
    CREATE OR REPLACE FUNCTION mail_index.messages_tsv_trigger() RETURNS trigger AS $$
    BEGIN
        NEW.tsv := to_tsvector('simple',
            coalesce(NEW.subject, '') || ' ' ||
            coalesce(NEW.from_addr, '') || ' ' ||
            coalesce(NEW.from_name, '') || ' ' ||
            coalesce(array_to_string(NEW.to_addrs, ' '), ''){extra}
        );
        RETURN NEW;
    END
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    addrs_type = sa.JSON() if is_sqlite else ARRAY(sa.Text())

    op.add_column("messages", sa.Column("cc_addrs", addrs_type), schema="mail_index")
    op.add_column("messages", sa.Column("bcc_addrs", addrs_type), schema="mail_index")
    op.add_column(
        "messages",
        sa.Column("recipients_indexed_at", sa.DateTime(timezone=True), nullable=True),
        schema="mail_index",
    )

    if not is_sqlite:
        op.execute(
            _TSV_FUNCTION.format(
                extra=" || ' ' ||\n"
                "            coalesce(array_to_string(NEW.cc_addrs, ' '), '') || ' ' ||\n"
                "            coalesce(array_to_string(NEW.bcc_addrs, ' '), '')"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if not is_sqlite:
        op.execute(_TSV_FUNCTION.format(extra=""))
    op.drop_column("messages", "recipients_indexed_at", schema="mail_index")
    op.drop_column("messages", "bcc_addrs", schema="mail_index")
    op.drop_column("messages", "cc_addrs", schema="mail_index")
