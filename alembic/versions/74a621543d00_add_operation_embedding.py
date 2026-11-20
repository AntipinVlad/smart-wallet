"""add operation embedding

Revision ID: 74a621543d00
Revises: 2bb50cf56e61
Create Date: 2026-11-19 16:05:41.118270

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "74a621543d00"
down_revision: str | Sequence[str] | None = "2bb50cf56e61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Nullable: the worker fills it after the operation is saved, and old operations wait for the backfill
    op.add_column("operation", sa.Column("embedding", VECTOR(1536), nullable=True))
    op.add_column("operation", sa.Column("embedding_model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("operation", "embedding_model")
    op.drop_column("operation", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
