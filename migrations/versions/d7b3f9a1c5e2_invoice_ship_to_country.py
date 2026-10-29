"""invoices freeze the Ship To's country (2.31.1 gate, round 3)

The printed invoice's Ship To read its country off the customer at print
time, under a ship-to street copied onto the invoice when it was made, so
an old invoice printed "2 Dock Rd Austin, TX 78702 Canada" once the
customer's ship country changed. The invoice now carries ship_to_country,
frozen with the ship-to street when it is made or moved to another
customer: the customer's own ship country (the Ship To is the customer's,
never the payer's). Rows from before are backfilled from each invoice's
own customer's ship country, which is what they printed until now.
downgrade drops the column.

Revision ID: d7b3f9a1c5e2
Revises: b4d8e2a6c7f1
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7b3f9a1c5e2"
down_revision: Union[str, None] = "b4d8e2a6c7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (sa.Column("ship_to_country", sa.String(100), nullable=True),)

# Each invoice's own customer's ship country, not the payer's: one UPDATE,
# portable to SQLite and PostgreSQL.
BACKFILL = (
    "UPDATE invoices SET ship_to_country = ("
    "SELECT c.ship_country FROM customers c WHERE c.id = invoices.customer_id"
    ") WHERE ship_to_country IS NULL"
)


def _add(insp, table, columns):
    if table not in insp.get_table_names():
        return False
    present = {c["name"] for c in insp.get_columns(table)}
    for column in columns:
        if column.name not in present:
            op.add_column(table, column.copy())
    return True


def _drop(insp, table, columns):
    if table not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns(table)}
    with op.batch_alter_table(table) as batch:
        for column in columns:
            if column.name in present:
                batch.drop_column(column.name)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _add(insp, "invoices", COLUMNS) and "customers" in insp.get_table_names():
        op.execute(sa.text(BACKFILL))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _drop(insp, "invoices", COLUMNS)
