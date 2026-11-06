"""normalize logins and store operation type as enum

Revision ID: f1996fe0786f
Revises: dc30326d7ecd
Create Date: 2026-11-06 12:31:01.129765

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1996fe0786f"
down_revision: str | Sequence[str] | None = "dc30326d7ecd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATION_TYPE = sa.Enum("EXPENSE", "INCOME", "TRANSFER", name="operationtype", native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    # Fails on logins that differ only in case: such users have to be merged first
    op.execute('UPDATE "user" SET login = lower(btrim(login))')
    # SQLAlchemy Enum stores member names, as for currency
    op.execute("UPDATE operation SET type = upper(type)")
    op.alter_column("operation", "type", existing_type=sa.String(), type_=OPERATION_TYPE, existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Logins stay normalized: the original spelling is lost
    op.alter_column("operation", "type", existing_type=OPERATION_TYPE, type_=sa.String(), existing_nullable=False)
    op.execute("UPDATE operation SET type = lower(type)")
