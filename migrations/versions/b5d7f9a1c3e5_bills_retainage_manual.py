"""bills.retainage_manual: a retainage set by hand is remembered (#597)

A bill converted from a commitment keeps a hand-set retainage across edits
of its lines, tax or date; one still at the commitment's rate follows the
rate. Telling the two apart by comparing the stored figure with the rate on
the old subtotal broke once the commitment's rate changed, so the bill now
records it. Existing bills default to false: every one of them follows the
rate until a request names retainage_held.

Revision ID: b5d7f9a1c3e5
Revises: a4c6e8f0b2d4
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5d7f9a1c3e5"
down_revision: Union[str, None] = "a4c6e8f0b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bills" not in insp.get_table_names():
        return
    if "retainage_manual" in {c["name"] for c in insp.get_columns("bills")}:
        return
    with op.batch_alter_table("bills") as batch:
        batch.add_column(
            sa.Column(
                "retainage_manual",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bills" not in insp.get_table_names():
        return
    if "retainage_manual" not in {c["name"] for c in insp.get_columns("bills")}:
        return
    with op.batch_alter_table("bills") as batch:
        batch.drop_column("retainage_manual")
