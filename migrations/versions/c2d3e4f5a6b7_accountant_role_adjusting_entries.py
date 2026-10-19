"""adjusting entries, who posted a journal entry, and an invited sign-in

Also (#345): an invited accountant signs in once with a one-time password
and must set their own (users.must_change_password), and a sign-in can be
given an end date after which it is refused (users.expires_on).

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


USER_COLUMNS = (
    sa.Column(
        "must_change_password",
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column("expires_on", sa.Date(), nullable=True),
)


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
    _add(insp, "users", USER_COLUMNS)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _drop(insp, "users", USER_COLUMNS)
    _drop(insp, "transactions", COLUMNS)
