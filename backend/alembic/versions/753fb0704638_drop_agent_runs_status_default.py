"""drop agent_runs status default

Revision ID: 753fb0704638
Revises: 70cb06e7d51a
Create Date: 2026-08-13 16:53:07.330005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '753fb0704638'
down_revision: Union[str, Sequence[str], None] = '70cb06e7d51a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Both current writers (RunTranscript.begin/save) already set status
    # explicitly, so this default has never actually been exercised. Kept as
    # 'complete' it fails in the wrong direction: a future insert path that
    # forgot to set status would have a genuinely-running row silently read as
    # finished. Dropping it makes an omission a NOT NULL violation at insert
    # time instead of a plausible-looking lie in the dashboard.
    op.alter_column('agent_runs', 'status', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('agent_runs', 'status', server_default='complete')
