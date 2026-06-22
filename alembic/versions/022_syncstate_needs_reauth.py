"""add needs_reauth to syncstate enum

A confirmed OAuth invalid_grant parks the account in needs_reauth so the
scheduler stops retrying a dead token. Additive PG enum value; SQLite
(tests) stores SyncState as VARCHAR so this is a no-op there.

Revision ID: 022
Revises: 021
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | Sequence[str] | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE syncstate ADD VALUE IF NOT EXISTS 'needs_reauth'")
    else:
        # Non-native backends (SQLite in tests) render sa.Enum as VARCHAR sized
        # to the longest value: widen for 'needs_reauth' (12 > 5) so migrated
        # schemas stay identical to the models (drift check compares them on
        # SQLite).
        with op.batch_alter_table("accounts") as batch:
            batch.alter_column(
                "sync_state",
                type_=sa.Enum(
                    "idle",
                    "syncing",
                    "error",
                    "needs_reauth",
                    name="syncstate",
                ),
                existing_type=sa.Enum(
                    "idle",
                    "syncing",
                    "error",
                    name="syncstate",
                ),
                existing_nullable=False,
            )


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value; downgrade is a no-op (the value
    # simply stays unused). Removing it would require recreating the type.
    pass
