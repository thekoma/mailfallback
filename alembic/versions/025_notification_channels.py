"""notification channels + accounts.last_notified_state

Per-user Apprise notification channels and the dedup marker that makes
notify-once-per-problem-state work.

Revision ID: 025
Revises: 024
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "025"
down_revision: str | Sequence[str] | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("apprise_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_channels_user_id", "notification_channels", ["user_id"])
    op.add_column("accounts", sa.Column("last_notified_state", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "last_notified_state")
    op.drop_index("ix_notification_channels_user_id", table_name="notification_channels")
    op.drop_table("notification_channels")
