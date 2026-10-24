"""transactions.vendor_id: the vendor a bank-feed line was posted to (#491)

A line added from a bank feed posted a register entry that carried its
payee as text only, so the vendor's page and payments list never saw it.
The entry now names its vendor. The column is optional and existing rows
take none; nothing changes for them.

SQLite adds the column with its REFERENCES clause in one ALTER TABLE (a
nullable column with no default may carry one), so the table is not
rebuilt; PostgreSQL adds the column, then the foreign key.

Revision ID: d7e8f9a0b1c2
Revises: c4e5d6f7a8b9
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c4e5d6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX = "ix_transactions_vendor_id"
FK = "fk_transactions_vendor_id"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "transactions" not in insp.get_table_names():
        return
    if "vendor_id" not in {c["name"] for c in insp.get_columns("transactions")}:
        if bind.dialect.name == "sqlite":
            op.execute(
                "ALTER TABLE transactions ADD COLUMN vendor_id INTEGER "
                "REFERENCES vendors (id)"
            )
        else:
            op.add_column(
                "transactions", sa.Column("vendor_id", sa.Integer(), nullable=True)
            )
            op.create_foreign_key(FK, "transactions", "vendors", ["vendor_id"], ["id"])
    if INDEX not in {i["name"] for i in insp.get_indexes("transactions")}:
        op.create_index(INDEX, "transactions", ["vendor_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "transactions" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("transactions")}:
        op.drop_index(INDEX, table_name="transactions")
    if "vendor_id" not in {c["name"] for c in insp.get_columns("transactions")}:
        return
    if bind.dialect.name == "sqlite":
        # SQLite 3.35+ drops a plain column in place; one with a REFERENCES
        # clause needs the table rebuilt, which batch mode does.
        with op.batch_alter_table("transactions") as batch:
            batch.drop_column("vendor_id")
    else:
        fks = {f["name"] for f in insp.get_foreign_keys("transactions")}
        for name in fks:
            if name and "vendor" in name:
                op.drop_constraint(name, "transactions", type_="foreignkey")
        op.drop_column("transactions", "vendor_id")
