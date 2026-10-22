"""saved invoice messages (#392)

The invoice_messages table: the saved notes an invoice can carry, kept
under Settings, one of them the default. The invoice form lists them
beside Notes and copies the chosen text in; the invoice stores the text
itself, so a message edited later leaves old invoices alone.

A new table only, guarded so a file create_all already made is left as
it is. Runs on SQLite and PostgreSQL.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "invoice_messages" in insp.get_table_names():
        return
    op.create_table(
        "invoice_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_invoice_messages_id", "invoice_messages", ["id"], unique=False)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "invoice_messages" in insp.get_table_names():
        op.drop_table("invoice_messages")
