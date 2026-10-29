"""transaction_lines.customer_id: the name on a register line (#606)

A deposit of two gifts from two donors is one bank entry with a line per
donor, so each income line carries the customer (donor) it came from, and
the donor reports count it. Nullable, indexed: every existing line has none.

Revision ID: a4c6e8f0b2d4
Revises: a7c3e5f1b9d2
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4c6e8f0b2d4"
down_revision: Union[str, None] = "a7c3e5f1b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transaction_lines" not in insp.get_table_names():
        return
    if "customer_id" in {c["name"] for c in insp.get_columns("transaction_lines")}:
        return
    with op.batch_alter_table("transaction_lines") as batch:
        batch.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_transaction_lines_customer",
            "customers",
            ["customer_id"],
            ["id"],
        )
        batch.create_index("ix_transaction_lines_customer_id", ["customer_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "transaction_lines" not in insp.get_table_names():
        return
    if "customer_id" not in {c["name"] for c in insp.get_columns("transaction_lines")}:
        return
    with op.batch_alter_table("transaction_lines") as batch:
        batch.drop_index("ix_transaction_lines_customer_id")
        batch.drop_constraint("fk_transaction_lines_customer", type_="foreignkey")
        batch.drop_column("customer_id")
