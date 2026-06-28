"""add payload_format to notification_channels

Per-channel toggle between plain-text and JSON notification bodies.
Defaults to "text" (existing behaviour unchanged).

Revision ID: 026
Revises: 025
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "026"
down_revision: str | Sequence[str] | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_channels",
        sa.Column("payload_format", sa.String(), nullable=False, server_default="text"),
    )


def downgrade() -> None:
    op.drop_column("notification_channels", "payload_format")
