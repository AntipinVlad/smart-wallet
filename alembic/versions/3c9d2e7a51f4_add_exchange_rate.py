"""add exchange rate

Revision ID: 3c9d2e7a51f4
Revises: 18b76a15caac
Create Date: 2026-11-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c9d2e7a51f4"
down_revision: str | Sequence[str] | None = "18b76a15caac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "exchange_rate",
        sa.Column("currency", sa.Enum("UAH", "USD", "EUR", name="currencyenum", native_enum=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("per_usd", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.PrimaryKeyConstraint("currency", "day"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("exchange_rate")
