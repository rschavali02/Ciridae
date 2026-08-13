"""agent run status

Revision ID: 70cb06e7d51a
Revises: 993f80e32aff
Create Date: 2026-08-12 22:00:51.587945

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70cb06e7d51a'
down_revision: Union[str, Sequence[str], None] = '993f80e32aff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 'running' | 'complete'. Unlike `vendors.approval_status`, this
    # `server_default` is kept rather than dropped after backfilling: it is the
    # standing default. Every row written before this column belongs to a run
    # that finished before it was inserted, and any future insert that omits the
    # column is likewise recording a run that is already over.
    op.add_column(
        'agent_runs',
        sa.Column('status', sa.String(), server_default='complete', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_runs', 'status')
