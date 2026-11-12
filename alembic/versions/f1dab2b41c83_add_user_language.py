"""add user language

Revision ID: f1dab2b41c83
Revises: 3c9d2e7a51f4
Create Date: 2026-11-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1dab2b41c83"
down_revision: str | Sequence[str] | None = "3c9d2e7a51f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The default only fills existing users: the app sets the language of new ones
    op.add_column(
        "user",
        sa.Column(
            "language",
            sa.Enum("UK", "EN", name="language", native_enum=False),
            nullable=False,
            server_default="UK",
        ),
    )
    op.alter_column("user", "language", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user", "language")
