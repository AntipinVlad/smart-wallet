"""add user timezone

Revision ID: 18b76a15caac
Revises: f1996fe0786f
Create Date: 2026-11-09 14:54:16.927864

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "18b76a15caac"
down_revision: str | Sequence[str] | None = "f1996fe0786f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The default only fills existing users: new ones must send their time zone
    op.add_column("user", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"))
    op.alter_column("user", "timezone", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user", "timezone")
