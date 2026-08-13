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
    """Upgrade schema."""
    op.add_column('invoices', sa.Column('currency', sa.String(length=3), nullable=True))
    op.add_column('purchase_orders', sa.Column('currency', sa.String(length=3), nullable=True))
    # `fields.py` is LLM-populated, so 'eur' and 'EUR' will both arrive. Two
    # spellings of one currency compare as a mismatch -- a false alarm in the
    # exact invoice-to-PO comparison the column exists to enable. Nothing
    # populates it yet, so the constraint is free to add now.
    op.create_check_constraint(
        'ck_invoices_currency_upper', 'invoices', "currency IS NULL OR currency = upper(currency)"
    )
    op.create_check_constraint(
        'ck_purchase_orders_currency_upper',
        'purchase_orders',
        "currency IS NULL OR currency = upper(currency)",
    )

    # `server_default` here is a backfill device, not a standing default. It
    # exists so vendor rows written before this column read 'active' rather than
    # NULL -- NULL would stop them resolving in `lookup_vendor`. It is dropped
    # immediately afterwards so a later insert that forgets `approval_status`
    # cannot silently produce a payable vendor, which is the exact outcome
    # 'pending_approval' exists to prevent. The model carries a Python-side
    # `default="pending_approval"` instead, so every human-authored insert site
    # states its trust explicitly.
    #
    # `alembic/env.py` sets neither `compare_type` nor `compare_server_default`,
    # so autogenerate will not notice if this and the model drift apart. Keep
    # them in step by hand.
    op.add_column(
        'vendors',
        sa.Column('approval_status', sa.String(), server_default='active', nullable=False),
    )
    op.alter_column('vendors', 'approval_status', server_default=None)

    op.add_column('vendors', sa.Column('created_by', sa.String(), server_default='human', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vendors', 'created_by')
    op.drop_column('vendors', 'approval_status')
    op.drop_constraint('ck_purchase_orders_currency_upper', 'purchase_orders', type_='check')
    op.drop_constraint('ck_invoices_currency_upper', 'invoices', type_='check')
    op.drop_column('purchase_orders', 'currency')
    op.drop_column('invoices', 'currency')
