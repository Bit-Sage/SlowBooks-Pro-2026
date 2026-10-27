"""transactions(source_type, source_id) index: the cash-basis backfill (#483)

The backfill finds the invoice and bill entries a payment applies to by
their source type and id; with no index that scanned `transactions` once
per payment (18 s for 2,000 payments, 189 s for 8,000, at every boot of a
file that needed it). Source lookups elsewhere (voids, drill-downs) use
the same pair.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-10-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_transactions_source"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transactions" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("transactions")}:
        return
    op.create_index(INDEX, "transactions", ["source_type", "source_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transactions" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("transactions")}:
        op.drop_index(INDEX, table_name="transactions")
