"""add extracted_vendor_name to invoices

Revision ID: c41f7a903de2
Revises: 6e89ae97d492
Create Date: 2026-08-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41f7a903de2'
down_revision: Union[str, Sequence[str], None] = '6e89ae97d492'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record the payee as printed, so an unresolved invoice still names a vendor."""
    op.add_column('invoices', sa.Column('extracted_vendor_name', sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the printed payee name."""
    op.drop_column('invoices', 'extracted_vendor_name')
