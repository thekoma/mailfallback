"""user allowed repositories

Per-user repository access, mirroring user_allowed_stores. The backfill
grants every existing repository to every existing user so behavior at
upgrade time is unchanged; admins prune afterwards. New users start empty.

Revision ID: 017
Revises: 016
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017"
down_revision: str | Sequence[str] | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_allowed_repositories",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["backup_destinations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "repository_id"),
    )
    op.execute(
        "INSERT INTO user_allowed_repositories (user_id, repository_id) "
        "SELECT u.id, r.id FROM users u CROSS JOIN backup_destinations r"
    )


def downgrade() -> None:
    op.drop_table("user_allowed_repositories")
