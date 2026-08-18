"""agent run status

Revision ID: 70cb06e7d51a
Revises: 993f80e32aff
Create Date: 2026-08-12 22:00:51.587945

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '70cb06e7d51a'
down_revision: Union[str, Sequence[str], None] = '993f80e32aff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the agent run status, backfilling existing rows as complete."""
    op.add_column(
        'agent_runs',
        sa.Column('status', sa.String(), server_default='complete', nullable=False),
    )


def downgrade() -> None:
    """Drop the agent run status column."""
    op.drop_column('agent_runs', 'status')
