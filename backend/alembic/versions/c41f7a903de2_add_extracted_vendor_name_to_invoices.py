"""add extracted_vendor_name to invoices

Revision ID: c41f7a903de2
Revises: 6e89ae97d492
Create Date: 2026-08-15

The payee as the document printed it, recorded at extraction time.

`vendor_id` is only set once a name resolves to an active row in the vendor
master, so an invoice from an unknown payee carried no record at all of who it
claimed to be from -- the name survived only inside `raw_text` and the agent's
transcript. That left the row orphaned from its vendor permanently: invisible
to the history and duplicate checks, which both filter on `vendor_id`, and
never adopted even after a human approved the vendor.

Nullable and not backfilled. Existing rows genuinely do not have this
information in a structured form, and inventing it by re-parsing `raw_text`
would write guesses into a column the vendor back-link matches on.
`payee_name` still falls back to the transcript for those rows.
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
    op.add_column('invoices', sa.Column('extracted_vendor_name', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('invoices', 'extracted_vendor_name')
