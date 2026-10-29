"""transactions.memo: the memo typed on an expense, kept on its own (#648)

An expense's memo was read back off its bank line, which holds the memo or,
with none typed, the payee. So a memo typed the same as the payee came back
blank, and a payee typed beside a vendor, with no memo, came back as the
memo. The column is optional: an expense saved from now on stores the memo
it was given ("" for none); rows from before take none and are read as
before. Other documents leave it empty. downgrade drops the column.

Revision ID: f3a9c7e1b5d4
Revises: c9e5a7b3d1f2
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3a9c7e1b5d4"
down_revision: Union[str, None] = "c9e5a7b3d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (sa.Column("memo", sa.Text(), nullable=True),)


def _add(insp, table, columns):
    if table not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns(table)}
    for column in columns:
        if column.name not in present:
            op.add_column(table, column.copy())


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
    _add(insp, "transactions", COLUMNS)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _drop(insp, "transactions", COLUMNS)
