"""construction change orders (#554)

Step 5 of the commercial GC layer, on top of #553. A change order is how a
locked budget, its contract and its commitments move, with an audit trail.
Additive and nullable (or defaulted) throughout, so a company with no change
orders sees none of it:

- change_orders — the document: kind (pco / owner / sub), status, funding
  (contract / allowance / contingency), the allowance SOV line it spends,
  the commitment a sub CO revises, the owner CO a sub CO backs, the PCO an
  owner CO was promoted from, and markup_pct (the CO's default markup).
- change_order_lines — material and labor priced separately, each with cost
  (qty × unit_cost) and a sell amount carrying its markup.
- jobs gains co_markup_pct (the default markup on owner change orders).
- sov_lines gains change_order_id (SET NULL) — the owner CO that owns a
  change_order-kind SOV line.

downgrade reverses.

Revision ID: a7c9e1f3b5d6
Revises: f3b5d7e9a2c4
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7c9e1f3b5d6"
down_revision: Union[str, None] = "f3b5d7e9a2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="draft"
        ),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column(
            "funding", sa.String(length=12), nullable=False, server_default="contract"
        ),
        sa.Column("allowance_sov_line_id", sa.Integer(), nullable=True),
        sa.Column("markup_pct", sa.Numeric(7, 4), nullable=True),
        sa.Column("commitment_id", sa.Integer(), nullable=True),
        sa.Column("owner_change_order_id", sa.Integer(), nullable=True),
        sa.Column("source_pco_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["allowance_sov_line_id"], ["sov_lines.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"], ["purchase_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_change_order_id"], ["change_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_pco_id"], ["change_orders.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_change_order_number"),
    )
    op.create_index(
        "ix_change_orders_job_id", "change_orders", ["job_id"], unique=False
    )

    op.create_table(
        "change_order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_order_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cost_type", sa.String(length=20), nullable=False),
        sa.Column("cost_code_id", sa.Integer(), nullable=True),
        sa.Column("trade_package_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("unit_cost", sa.Numeric(15, 4), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("markup_pct", sa.Numeric(7, 4), nullable=True),
        sa.Column("sell_amount", sa.Numeric(15, 2), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_order_id"], ["change_orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cost_code_id"], ["cost_codes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["trade_package_id"], ["trade_packages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_change_order_lines_change_order_id",
        "change_order_lines",
        ["change_order_id"],
        unique=False,
    )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("co_markup_pct", sa.Numeric(7, 4), nullable=True))

    with op.batch_alter_table("sov_lines") as batch:
        batch.add_column(sa.Column("change_order_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sov_line_change_order",
            "change_orders",
            ["change_order_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sov_lines") as batch:
        batch.drop_constraint("fk_sov_line_change_order", type_="foreignkey")
        batch.drop_column("change_order_id")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("co_markup_pct")

    op.drop_index(
        "ix_change_order_lines_change_order_id", table_name="change_order_lines"
    )
    op.drop_table("change_order_lines")
    op.drop_index("ix_change_orders_job_id", table_name="change_orders")
    op.drop_table("change_orders")
