"""staging_push restore mode

Staging pushes reuse restore_jobs with restore_mode='staging_push'
(selected_uids doubles as the {folder: [staged_filename]} manifest).
PostgreSQL stores restore_mode as the native enum type "restoremode"
(created by 007), so the new value needs ALTER TYPE — mirrors 008,
which extended "jobstatus" the same way. Non-native backends (SQLite
in tests) render the column as VARCHAR sized to the longest value, so
it is widened in place (9 -> 12 for 'staging_push') via batch alter to
keep migrated schemas identical to the models.

Revision ID: 020
Revises: 019
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: str | Sequence[str] | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE restoremode ADD VALUE IF NOT EXISTS 'staging_push'")
    else:
        # Non-native backends render sa.Enum as VARCHAR sized to the longest
        # value: widen for 'staging_push' (12 > 9) so migrated schemas stay
        # identical to the models (drift check compares them on SQLite).
        with op.batch_alter_table("restore_jobs") as batch:
            batch.alter_column(
                "restore_mode",
                type_=sa.Enum("full", "folder", "selection", "staging_push", name="restoremode"),
                existing_nullable=False,
            )


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type; the extra value is
    # harmless to older code (nothing creates staging_push jobs there).
    pass
