"""add wallet balance not negative check

Revision ID: 67e4be922f02
Revises: f49fdade1d0e
Create Date: 2026-11-03 14:46:53.995553

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "67e4be922f02"
down_revision: str | Sequence[str] | None = "f49fdade1d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Written by hand: autogenerate does not detect check constraints
def upgrade() -> None:
    """Upgrade schema."""
    # Fails if a wallet already has a negative balance: such data has to be fixed first
    op.create_check_constraint("ck_wallet_balance_not_negative", "wallet", "balance >= 0")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_wallet_balance_not_negative", "wallet", type_="check")
