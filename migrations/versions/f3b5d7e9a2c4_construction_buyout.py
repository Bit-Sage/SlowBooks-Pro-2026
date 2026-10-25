"""construction buyout and commitments (#553)

Step 4 of the commercial GC layer, on top of #552. A commitment is a purchase
order — no new document type. Additive and nullable (or defaulted) throughout,
so a company with no commitments sees none of it:

- purchase_orders gains commitment_type (po / subcontract, default po),
  trade_package_id (SET NULL), retainage_pct (held on every bill from the
  commitment) and scope_notes.
- purchase_order_lines gains billed_amount (sum billed so far; the line's
  amount is its scheduled value). remaining = amount - billed_amount.
- bills gains retainage_held (credited to Retainage Payable, 2050, not A/P)
  and lien_waiver_status.
- bill_lines gains purchase_order_line_id (SET NULL) — the back link partial
  billing needs.

Backfill: a CLOSED purchase order that already became a bill (bills.po_id)
has each of its lines fully billed, so billed_amount = amount for them.
downgrade reverses.

Revision ID: f3b5d7e9a2c4
Revises: e2a4c6d8f0b1
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3b5d7e9a2c4"
down_revision: Union[str, None] = "e2a4c6d8f0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_orders") as batch:
        batch.add_column(
            sa.Column(
                "commitment_type",
                sa.String(length=12),
                nullable=False,
                server_default="po",
            )
        )
        batch.add_column(sa.Column("trade_package_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retainage_pct", sa.Numeric(5, 2), nullable=True))
        batch.add_column(sa.Column("scope_notes", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_po_trade_package",
            "trade_packages",
            ["trade_package_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("purchase_order_lines") as batch:
        batch.add_column(
            sa.Column(
                "billed_amount",
                sa.Numeric(15, 2),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("bills") as batch:
        batch.add_column(
            sa.Column(
                "retainage_held",
                sa.Numeric(15, 2),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("lien_waiver_status", sa.String(length=16), nullable=True)
        )

    with op.batch_alter_table("bill_lines") as batch:
        batch.add_column(
            sa.Column("purchase_order_line_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_bill_line_po_line",
            "purchase_order_lines",
            ["purchase_order_line_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # A closed PO that already became a bill was billed in full: its lines'
    # billed_amount equals their scheduled value.
    op.execute("""
        UPDATE purchase_order_lines
        SET billed_amount = amount
        WHERE purchase_order_id IN (
            SELECT po.id FROM purchase_orders po
            WHERE po.status = 'CLOSED'
              AND EXISTS (SELECT 1 FROM bills b WHERE b.po_id = po.id)
        )
        """)


def downgrade() -> None:
    with op.batch_alter_table("bill_lines") as batch:
        batch.drop_constraint("fk_bill_line_po_line", type_="foreignkey")
        batch.drop_column("purchase_order_line_id")

    with op.batch_alter_table("bills") as batch:
        batch.drop_column("lien_waiver_status")
        batch.drop_column("retainage_held")

    with op.batch_alter_table("purchase_order_lines") as batch:
        batch.drop_column("billed_amount")

    with op.batch_alter_table("purchase_orders") as batch:
        batch.drop_constraint("fk_po_trade_package", type_="foreignkey")
        batch.drop_column("scope_notes")
        batch.drop_column("retainage_pct")
        batch.drop_column("trade_package_id")
        batch.drop_column("commitment_type")
