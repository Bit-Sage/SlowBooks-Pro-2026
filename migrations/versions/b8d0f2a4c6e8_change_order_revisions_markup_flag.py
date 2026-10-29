"""change orders: per-job numbers, PCO revisions, roll-up, markup flag (#554)

Review follow-up on construction change orders. Additive; existing rows keep
their numbers and their behaviour:

- change_orders.number widens to 80 chars (job number + CO-001 + revision).
- apply_markup (default true): whether markup applies. Existing allowance COs
  are set off (they never carried markup); contingency COs keep what they had.
- revision / supersedes_id: a PCO revision replaces the one before it, which is
  kept as history.
- promoted_to_id: the owner CO a PCO was rolled into (several PCOs may share
  one).

downgrade reverses.

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1f3b5d6
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8d0f2a4c6e8"
down_revision: Union[str, None] = "a7c9e1f3b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("change_orders") as batch:
        batch.alter_column(
            "number",
            existing_type=sa.String(length=20),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
        batch.add_column(
            sa.Column(
                "apply_markup",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("supersedes_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("promoted_to_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_co_supersedes",
            "change_orders",
            ["supersedes_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_co_promoted_to",
            "change_orders",
            ["promoted_to_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # An allowance never carried markup; record that on the existing rows.
    op.execute(
        sa.text(
            "UPDATE change_orders SET apply_markup = :off WHERE funding = 'allowance'"
        ).bindparams(sa.bindparam("off", False, type_=sa.Boolean()))
    )


def downgrade() -> None:
    with op.batch_alter_table("change_orders") as batch:
        batch.drop_constraint("fk_co_promoted_to", type_="foreignkey")
        batch.drop_constraint("fk_co_supersedes", type_="foreignkey")
        batch.drop_column("promoted_to_id")
        batch.drop_column("supersedes_id")
        batch.drop_column("revision")
        batch.drop_column("apply_markup")
        batch.alter_column(
            "number",
            existing_type=sa.String(length=80),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
