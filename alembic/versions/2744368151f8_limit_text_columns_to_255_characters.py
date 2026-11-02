"""limit text columns to 255 characters

Revision ID: 2744368151f8
Revises: ab5c1b262ae3
Create Date: 2026-11-02 16:17:01.991479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2744368151f8'
down_revision: Union[str, Sequence[str], None] = 'ab5c1b262ae3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Autogenerate does not detect a length added to String, so these are written by hand
COLUMNS = [
    ('user', 'login', False),
    ('wallet', 'name', False),
    ('operation', 'category', True),
    ('operation', 'subcategory', True),
    ('operation', 'description', True),
]


def upgrade() -> None:
    """Upgrade schema."""
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, type_=sa.String(255), existing_type=sa.String(), existing_nullable=nullable)


def downgrade() -> None:
    """Downgrade schema."""
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, type_=sa.String(), existing_type=sa.String(255), existing_nullable=nullable)
