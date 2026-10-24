"""reconciliations.max_statement_date: the session's high-water date

The ticked-after-the-date notice (#400) counted every cleared, unreconciled
line dated after the statement date, which includes bank-feed matches and
register clears, so it showed on nearly every reconcile of a feed-linked
account. The notice should only count lines this session left behind by
moving its date earlier.

To tell those apart we remember the latest statement date a session ever
had. A line counts only if it is dated after the current date but on or
before that high-water date — it was in reach of this session before the
date moved. A feed match dated after the date, with the date never moved,
is past the high-water mark too, so it no longer counts.

Existing rows take their current statement_date, which makes the notice
report 0 for them until their date is moved.

Revision ID: a1f2e3d4c5b6
Revises: d3e4f5a6b7c8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1f2e3d4c5b6"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMN = sa.Column("max_statement_date", sa.Date(), nullable=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "reconciliations" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("reconciliations")}
    if COLUMN.name not in present:
        op.add_column("reconciliations", COLUMN.copy())
        op.execute(
            "UPDATE reconciliations "
            "SET max_statement_date = statement_date "
            "WHERE max_statement_date IS NULL"
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "reconciliations" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("reconciliations")}
    if COLUMN.name in present:
        with op.batch_alter_table("reconciliations") as batch:
            batch.drop_column(COLUMN.name)
