"""construction estimating — bid shape, markups, and a locked budget (#551)

Step 2 of the commercial GC layer, on top of #550. Additive and nullable
(or defaulted) throughout, so an ordinary company sees none of it:

- estimate_lines gains the bid shape — trade_package_id (which package the
  line bids under, SET NULL if the package goes away), line_kind ("base" /
  "allowance" / "alternate" / "unit_price", default "base"), unit (SF, LF,
  EA…) and alternate_accepted (default false).
- estimates gains the four compounding markups gc_pct / insurance_pct /
  fee_pct / bond_pct (percent values, default 0), markup_allowances (default
  false: markups skip allowance lines unless on) and accepted_at.
- job_budgets gains locked (default false): an accepted estimate locks the
  rows it seeded so only a change order moves them.

Nothing is backfilled. downgrade reverses.

Revision ID: d9f6b2c4e7a1
Revises: f2b3c4d5e6a7
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9f6b2c4e7a1"
down_revision: Union[str, None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("estimate_lines") as batch:
        batch.add_column(sa.Column("trade_package_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "line_kind",
                sa.String(length=12),
                nullable=False,
                server_default="base",
            )
        )
        batch.add_column(sa.Column("unit", sa.String(length=20), nullable=True))
        batch.add_column(
            sa.Column(
                "alternate_accepted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_foreign_key(
            "fk_estimate_lines_trade_package",
            "trade_packages",
            ["trade_package_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("estimates") as batch:
        for col in ("gc_pct", "insurance_pct", "fee_pct", "bond_pct"):
            batch.add_column(
                sa.Column(col, sa.Numeric(7, 4), nullable=False, server_default="0")
            )
        batch.add_column(
            sa.Column(
                "markup_allowances",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table("job_budgets") as batch:
        batch.add_column(
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    # An invoice converted from an estimate carries the line's cost basis
    # (#551) — the estimate's per-line cost type and unit cost, dropped until
    # now. NULL on an ordinary invoice line.
    with op.batch_alter_table("invoice_lines") as batch:
        batch.add_column(sa.Column("cost_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.drop_column("unit_cost")
        batch.drop_column("cost_type")

    with op.batch_alter_table("job_budgets") as batch:
        batch.drop_column("locked")

    with op.batch_alter_table("estimates") as batch:
        batch.drop_column("accepted_at")
        batch.drop_column("markup_allowances")
        for col in ("bond_pct", "fee_pct", "insurance_pct", "gc_pct"):
            batch.drop_column(col)

    with op.batch_alter_table("estimate_lines") as batch:
        batch.drop_constraint("fk_estimate_lines_trade_package", type_="foreignkey")
        batch.drop_column("alternate_accepted")
        batch.drop_column("unit")
        batch.drop_column("line_kind")
        batch.drop_column("trade_package_id")
