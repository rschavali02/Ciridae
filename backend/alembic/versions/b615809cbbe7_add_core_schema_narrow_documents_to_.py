"""add core schema; narrow documents to policy-only

Revision ID: b615809cbbe7
Revises: ac2014d06159
Create Date: 2026-08-02 20:17:43.376426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b615809cbbe7'
down_revision: Union[str, Sequence[str], None] = 'ac2014d06159'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the core invoice tables, and narrow documents to policy chunks only."""
    op.create_table('vendors',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('normalized_name', sa.String(), nullable=False),
    sa.Column('bank_details', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('invoices',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('vendor_id', sa.UUID(), nullable=True),
    sa.Column('invoice_number', sa.String(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('po_number', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('confidence_score', sa.Float(), nullable=True),
    sa.Column('raw_pdf_path', sa.String(), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('agent_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('transcript', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('decision', sa.String(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('before_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('line_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # Safe to empty: the corpus is re-derived from the policy PDF on load.
    op.execute("TRUNCATE TABLE documents")
    op.add_column('documents', sa.Column('section', sa.Text(), nullable=False))
    op.drop_column('documents', 'invoice_id')
    op.drop_column('documents', 'doc_type')


def downgrade() -> None:
    """Drop the core invoice tables and restore the original documents columns."""
    op.add_column('documents', sa.Column('doc_type', sa.TEXT(), autoincrement=False, nullable=False))
    op.add_column('documents', sa.Column('invoice_id', sa.UUID(), autoincrement=False, nullable=True))
    op.drop_column('documents', 'section')
    op.drop_table('line_items')
    op.drop_table('audit_log')
    op.drop_table('agent_runs')
    op.drop_table('invoices')
    op.drop_table('vendors')
