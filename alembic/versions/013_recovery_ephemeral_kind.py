"""recovery ephemeral kind

Adds three columns to the recoveries table:
- kind: persistent (today's behaviour) | ephemeral (TTL-driven, auto-cleanup)
- last_accessed_at: bumped on each access; ephemerals past TTL get swept
- ttl_minutes: NULL means no TTL (persistent never expires)

Existing rows backfill to kind=persistent, ttl_minutes=NULL.

Revision ID: 013
Revises: 012
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    recovery_kind = sa.Enum("persistent", "ephemeral", name="recoverykind")
    recovery_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "recoveries",
        sa.Column("kind", recovery_kind, nullable=False, server_default="persistent"),
    )
    op.add_column(
        "recoveries",
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column("recoveries", sa.Column("ttl_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("recoveries", "ttl_minutes")
    op.drop_column("recoveries", "last_accessed_at")
    op.drop_column("recoveries", "kind")
    sa.Enum(name="recoverykind").drop(op.get_bind(), checkfirst=False)
