"""add pg_trgm extension

Revision ID: 2bb50cf56e61
Revises: f1dab2b41c83
Create Date: 2026-11-19 14:32:12.823431

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bb50cf56e61"
down_revision: str | Sequence[str] | None = "f1dab2b41c83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # word_similarity for search: ships with Postgres, but every database turns it on itself
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
