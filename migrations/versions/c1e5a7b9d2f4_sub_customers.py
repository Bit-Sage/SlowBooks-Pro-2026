"""customers gain parent_customer_id and bill_with_parent (#638)

A sub-customer is a customer under another, one level deep: QuickBooks'
"Parent:Sub" (a tenant under a property). bill_with_parent puts the sub's
invoices on the parent's A/R. Both nullable or defaulted, nothing backfilled:
every customer from before this release stays a top-level one billed on its
own. downgrade drops the columns.

Revision ID: c1e5a7b9d2f4
Revises: b8d4f2a6c1e3
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1e5a7b9d2f4"
down_revision: Union[str, None] = "b8d4f2a6c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("parent_customer_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "bill_with_parent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_foreign_key(
            "fk_customers_parent_customer",
            "customers",
            ["parent_customer_id"],
            ["id"],
        )
        batch.create_index("ix_customers_parent_customer_id", ["parent_customer_id"])


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_parent_customer_id")
        batch.drop_constraint("fk_customers_parent_customer", type_="foreignkey")
        batch.drop_column("bill_with_parent")
        batch.drop_column("parent_customer_id")
