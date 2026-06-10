"""attachment restic password

Attached foreign prefixes may be encrypted with a different restic password
than their repository. NULL keeps the repository's password.

Revision ID: 016
Revises: 015
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | Sequence[str] | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repository_attachments", sa.Column("restic_password", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("repository_attachments", "restic_password")
