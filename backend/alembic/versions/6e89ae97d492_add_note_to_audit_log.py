"""add note to audit log

Revision ID: 6e89ae97d492
Revises: 753fb0704638
Create Date: 2026-08-13 18:33:26.902528

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '6e89ae97d492'
down_revision: Union[str, Sequence[str], None] = '753fb0704638'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the reviewer's note, and stamp audit rows at write time."""
    op.add_column('audit_log', sa.Column('note', sa.Text(), nullable=True))
    op.alter_column(
        'audit_log', 'created_at', server_default=sa.text('clock_timestamp()')
    )


def downgrade() -> None:
    """Remove the note and revert to transaction-start timestamps."""
    op.alter_column('audit_log', 'created_at', server_default=sa.text('now()'))
    op.drop_column('audit_log', 'note')
