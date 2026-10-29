"""progress billing review fixes: tax toggle, contract snapshot, lien waivers (#576)

- pay_applications gains charge_tax (tax on by default, switchable per
  application) and the contract sums as of approval (nullable; a draft and
  older approved rows read the live job).
- lien_waivers — one waiver per payment and type (conditional / unconditional,
  progress / final).

downgrade reverses.

Revision ID: b4d6f8a0c2e3
Revises: e5c9a1f7b2d4
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b4d6f8a0c2e3"
down_revision: Union[str, None] = "e5c9a1f7b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pay_applications") as batch:
        batch.add_column(
            sa.Column(
                "charge_tax",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column("contract_sum_original", sa.Numeric(15, 2), nullable=True)
        )
        batch.add_column(
            sa.Column("contract_sum_to_date", sa.Numeric(15, 2), nullable=True)
        )

    op.create_table(
        "lien_waivers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("pay_application_id", sa.Integer(), nullable=True),
        sa.Column("waiver_type", sa.String(length=24), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("through_date", sa.Date(), nullable=True),
        sa.Column("signed_by", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["pay_application_id"], ["pay_applications.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_id", "waiver_type", name="uq_lien_waiver_payment_type"
        ),
    )
    op.create_index(
        "ix_lien_waivers_payment_id", "lien_waivers", ["payment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_lien_waivers_payment_id", table_name="lien_waivers")
    op.drop_table("lien_waivers")
    with op.batch_alter_table("pay_applications") as batch:
        batch.drop_column("contract_sum_to_date")
        batch.drop_column("contract_sum_original")
        batch.drop_column("charge_tax")
