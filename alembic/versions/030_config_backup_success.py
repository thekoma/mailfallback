"""Preserve configuration backup last success for metrics (#308).

Revision ID: 030
Revises: 029
"""

import sqlalchemy as sa

from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backup_destinations",
        sa.Column("last_config_backup_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A failed last attempt cannot reveal the preceding successful backup.
    op.execute(
        "UPDATE backup_destinations SET last_config_backup_success_at = last_config_backup_at "
        "WHERE last_config_backup_status = 'ok' AND last_config_backup_success_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("backup_destinations", "last_config_backup_success_at")
