"""add note to audit log

Revision ID: 6e89ae97d492
Revises: 753fb0704638
Create Date: 2026-08-13 18:33:26.902528

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e89ae97d492'
down_revision: Union[str, Sequence[str], None] = '753fb0704638'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('audit_log', sa.Column('note', sa.Text(), nullable=True))
    # now() returns the enclosing transaction's start time, identical for
    # every row written inside one transaction -- two decisions on the same
    # invoice, committed together, would tie in a log whose whole purpose is
    # chronological order. clock_timestamp() is the actual wall-clock time at
    # the moment each row is written.
    op.alter_column(
        'audit_log', 'created_at', server_default=sa.text('clock_timestamp()')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('audit_log', 'created_at', server_default=sa.text('now()'))
    op.drop_column('audit_log', 'note')
