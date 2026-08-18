"""vendor status and currency columns

Revision ID: 993f80e32aff
Revises: 2561082bd6b1
Create Date: 2026-08-12 20:04:26.395874

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '993f80e32aff'
down_revision: Union[str, Sequence[str], None] = '2561082bd6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add currency to invoices and POs, and approval tracking to vendors."""
    op.add_column('invoices', sa.Column('currency', sa.String(length=3), nullable=True))
    op.add_column('purchase_orders', sa.Column('currency', sa.String(length=3), nullable=True))
    op.create_check_constraint(
        'ck_invoices_currency_upper', 'invoices', "currency IS NULL OR currency = upper(currency)"
    )
    op.create_check_constraint(
        'ck_purchase_orders_currency_upper',
        'purchase_orders',
        "currency IS NULL OR currency = upper(currency)",
    )

    # The default backfills existing vendors as active, then is dropped so a
    # future insert cannot create a payable vendor by omission.
    op.add_column(
        'vendors',
        sa.Column('approval_status', sa.String(), server_default='active', nullable=False),
    )
    op.alter_column('vendors', 'approval_status', server_default=None)

    op.add_column('vendors', sa.Column('created_by', sa.String(), server_default='human', nullable=False))


def downgrade() -> None:
    """Remove the currency and vendor approval columns."""
    op.drop_column('vendors', 'created_by')
    op.drop_column('vendors', 'approval_status')
    op.drop_constraint('ck_purchase_orders_currency_upper', 'purchase_orders', type_='check')
    op.drop_constraint('ck_invoices_currency_upper', 'invoices', type_='check')
    op.drop_column('purchase_orders', 'currency')
    op.drop_column('invoices', 'currency')
