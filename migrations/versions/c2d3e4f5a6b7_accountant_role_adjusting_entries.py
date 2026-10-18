"""adjusting entries, and who posted a journal entry

An outside accountant's adjustments (#341) are journal entries like any
other, so the owner could not tell them from their own. Two nullable-safe
columns on transactions: is_adjusting (the Adjusting entry box on New
Journal Entry, on by default for the Accountant role of #340, and what the
Adjusting Journal Entries report lists) and posted_by (the sign-in that
posted the entry, which the entry's view shows and which lets an accountant
void only what it posted). Existing rows are not adjusting and have no
poster.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    sa.Column(
        "is_adjusting",
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column("posted_by", sa.String(length=100), nullable=True),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transactions" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("transactions")}
    for column in COLUMNS:
        if column.name not in present:
            op.add_column("transactions", column.copy())


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transactions" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("transactions")}
    with op.batch_alter_table("transactions") as batch:
        for column in COLUMNS:
            if column.name in present:
                batch.drop_column(column.name)
