"""invoice message kind (#415)

The invoice_messages table grows a ``kind`` column so one list can hold
both the invoice messages (#392) and the ministry's giving-statement
opening messages. Existing rows are invoices: the column's server
default is 'invoice', so a file upgraded from #392 keeps every saved
message as an invoice message and the invoice form sees no change.

Guarded so a file whose create_all already made the column is left as it
is. Runs on SQLite and PostgreSQL.

Revision ID: f9a0b1c2d3e4
Revises: e4f5a6b7c8d9
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "invoice_messages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("invoice_messages")}
    if "kind" in cols:
        return
    op.add_column(
        "invoice_messages",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="invoice",
        ),
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "invoice_messages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("invoice_messages")}
    if "kind" in cols:
        op.drop_column("invoice_messages", "kind")
