"""widen accounts.maildir_size_bytes to BigInteger

A mailbox larger than the PostgreSQL INTEGER max (~2.1 GB) overflowed the
collect_account_stats UPDATE (NumericValueOutOfRange) — stats never persisted
for large accounts (a ~15 GB Gmail account, 2026-06-22). BigInteger fixes it.
SQLite (tests) already treats INTEGER as 64-bit, so this is a no-op there.

Revision ID: 023
Revises: 022
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "023"
down_revision: str | Sequence[str] | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column(
            "maildir_size_bytes",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column(
            "maildir_size_bytes",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
