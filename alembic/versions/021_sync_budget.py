"""sync budget / pause / initial-sync columns

Throttle-aware first full sync: accounts gain a per-day byte ledger
(traffic_date + bytes_synced_today, UTC day), an optional budget override
(daily_sync_budget_mb: NULL → provider default, 0 → unlimited), a
self-recovering pause gate for the scheduler (sync_paused_until +
pause_reason — pauses are NOT errors), and the initial-sync regime
markers (initial_sync_completed_at + initial_sync_total_messages, the
progress denominator). sync_jobs gains failure_kind: throttled |
budget_paused | transient | interrupted | error — a plain string by
design, so new kinds need no migration.

Backfill: accounts that have ever completed a sync skip the initial-sync
regime (initial_sync_completed_at = last_sync_at).

Revision ID: 021
Revises: 020
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: str | Sequence[str] | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("traffic_date", sa.Date(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("bytes_synced_today", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("accounts", sa.Column("daily_sync_budget_mb", sa.Integer(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("sync_paused_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("accounts", sa.Column("pause_reason", sa.String(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("initial_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("initial_sync_total_messages", sa.Integer(), nullable=True),
    )
    op.add_column("sync_jobs", sa.Column("failure_kind", sa.String(), nullable=True))
    # Existing healthy accounts skip the initial-sync regime: any completed
    # sync counts as "initial sync done".
    op.execute(
        "UPDATE accounts SET initial_sync_completed_at = last_sync_at "
        "WHERE last_sync_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("sync_jobs", "failure_kind")
    op.drop_column("accounts", "initial_sync_total_messages")
    op.drop_column("accounts", "initial_sync_completed_at")
    op.drop_column("accounts", "pause_reason")
    op.drop_column("accounts", "sync_paused_until")
    op.drop_column("accounts", "daily_sync_budget_mb")
    op.drop_column("accounts", "bytes_synced_today")
    op.drop_column("accounts", "traffic_date")
