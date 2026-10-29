"""invoices freeze whom they were billed to (#638; 2.31.1 gate, W-59)

The printed invoice read its Bill To name and country off the customer at
print time, over a street copied onto the invoice when it was made, so a
tenant moved to another property, or billed on its own, afterwards printed
the new name and country over the old property's street. The invoice now
carries bill_to_customer_id, bill_to_name and bill_to_country, frozen with
the street when it is made: the parent of a sub-customer billed with it,
else the customer itself. Rows from before are backfilled from the
customer each one is billed to at the upgrade (the parent's name and
country for a sub-customer billed with its parent, else the customer's
own), which is what they printed until now. downgrade drops the columns.

Revision ID: b4d8e2a6c7f1
Revises: f3a9c7e1b5d4
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b4d8e2a6c7f1"
down_revision: Union[str, None] = "f3a9c7e1b5d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    sa.Column("bill_to_customer_id", sa.Integer(), nullable=True),
    sa.Column("bill_to_name", sa.String(200), nullable=True),
    sa.Column("bill_to_country", sa.String(100), nullable=True),
)

# Whom each invoice is billed to today: the parent of a sub-customer
# billed with it, else the customer. One UPDATE, portable to SQLite and
# PostgreSQL (bill_with_parent is a boolean on both).
_BILLED = (
    "SELECT CASE WHEN c.bill_with_parent AND c.parent_customer_id IS NOT NULL "
    "THEN {parent} ELSE {own} END "
    "FROM customers c LEFT JOIN customers p ON p.id = c.parent_customer_id "
    "WHERE c.id = invoices.customer_id"
)
BACKFILL = (
    "UPDATE invoices SET "
    "bill_to_customer_id = (" + _BILLED.format(parent="p.id", own="c.id") + "), "
    "bill_to_name = (" + _BILLED.format(parent="p.name", own="c.name") + "), "
    "bill_to_country = ("
    + _BILLED.format(parent="p.bill_country", own="c.bill_country")
    + ") WHERE bill_to_name IS NULL"
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
