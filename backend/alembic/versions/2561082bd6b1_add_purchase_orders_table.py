"""add purchase_orders table

Revision ID: 2561082bd6b1
Revises: b615809cbbe7
Create Date: 2026-08-02 21:57:37.488213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2561082bd6b1'
down_revision: Union[str, Sequence[str], None] = 'b615809cbbe7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the purchase_orders table."""
    op.create_table('purchase_orders',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('po_number', sa.String(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('po_number')
    )


def downgrade() -> None:
    """Drop the purchase_orders table."""
    op.drop_table('purchase_orders')
