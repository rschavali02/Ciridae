"""drop agent_runs status default

Revision ID: 753fb0704638
Revises: 70cb06e7d51a
Create Date: 2026-08-13 16:53:07.330005

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '753fb0704638'
down_revision: Union[str, Sequence[str], None] = '70cb06e7d51a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the status default, so an omitted status fails instead of reading as complete."""
    op.alter_column('agent_runs', 'status', server_default=None)


def downgrade() -> None:
    """Restore the complete default on agent run status."""
    op.alter_column('agent_runs', 'status', server_default='complete')
