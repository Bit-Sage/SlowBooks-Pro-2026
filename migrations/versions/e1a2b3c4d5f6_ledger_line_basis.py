"""transaction_lines.basis: which basis a ledger line counts on (#483)

Reports on a cash basis (docs/design/cash-basis.md): every line says
whether its figure counts on both bases, on the accrual basis only (an
invoice's or bill's lines) or on the cash basis only (the recognition a
payment writes). Every existing row is "both", and a row an older build
inserts takes the server default, so a file opened by both builds stays
readable. The accrual tags and the cash lines for existing payments are
written by the start-up backfill (app/services/cash_basis.py), which runs
on every start and writes nothing the second time.

No index: a filter of "basis != 'cash'" keeps almost every row.

Revision ID: e1a2b3c4d5f6
Revises: c8e4a2f1b3d5
Create Date: 2026-10-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, None] = "c8e4a2f1b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "transaction_lines" not in insp.get_table_names():
        return
    if "basis" in {c["name"] for c in insp.get_columns("transaction_lines")}:
        return
    op.add_column(
        "transaction_lines",
        sa.Column("basis", sa.String(10), nullable=False, server_default="both"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "transaction_lines" not in insp.get_table_names():
        return
    if "basis" not in {c["name"] for c in insp.get_columns("transaction_lines")}:
        return
    # The cash lines mean nothing to a build without the column: a payment
    # entry would read Dr bank, Cr A/R, Dr A/R, Cr income. Take them out.
    op.execute("DELETE FROM transaction_lines WHERE basis = 'cash'")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("transaction_lines") as batch:
            batch.drop_column("basis")
    else:
        op.drop_column("transaction_lines", "basis")
