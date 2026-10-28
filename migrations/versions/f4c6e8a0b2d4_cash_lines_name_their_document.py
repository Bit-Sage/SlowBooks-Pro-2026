"""a cash recognition line names the invoice or bill it recognizes (#483)

Step 3 of reports on a cash basis. A payment's cash lines are the income or
expense of the documents it paid; editing a paid document recomputes them,
which needs to find them. transaction_lines gains two nullable columns,
recognizes_invoice_id and recognizes_bill_id, each indexed and carrying no
foreign key (a line is history: deleting a document must not be blocked or
cascade into a payment's entry). Lines written before this revision stay
NULL; the edit finds those by the rule in app/services/cash_basis.py.

Revision ID: f4c6e8a0b2d4
Revises: c6e8f0a2d4b6
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4c6e8a0b2d4"
down_revision: Union[str, None] = "c6e8f0a2d4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "transaction_lines"
COLUMNS = ("recognizes_invoice_id", "recognizes_bill_id")


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    have = _existing()
    for name in COLUMNS:
        if name not in have:
            op.add_column(TABLE, sa.Column(name, sa.Integer(), nullable=True))
        indexed = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABLE)}
        if f"ix_{TABLE}_{name}" not in indexed:
            op.create_index(f"ix_{TABLE}_{name}", TABLE, [name])


def downgrade() -> None:
    have = _existing()
    for name in COLUMNS:
        if name in have:
            indexed = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABLE)}
            if f"ix_{TABLE}_{name}" in indexed:
                op.drop_index(f"ix_{TABLE}_{name}", table_name=TABLE)
            with op.batch_alter_table(TABLE) as batch:
                batch.drop_column(name)
