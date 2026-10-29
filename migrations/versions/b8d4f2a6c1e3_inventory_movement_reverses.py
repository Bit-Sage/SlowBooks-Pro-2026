"""inventory_movements gains reverses_movement_id (#622)

A sale's reversal (void, quantity-down edit) now records which SALE movement
it reverses, one VOID row per original posting, so a later reversal reverses
each posting where it went on and at its own cost. Nullable, nothing
backfilled: rows from before this release keep working through the older
newest-first accounting. downgrade drops the column.

Revision ID: b8d4f2a6c1e3
Revises: b5d7e9f1a3c5
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8d4f2a6c1e3"
down_revision: Union[str, None] = "b5d7e9f1a3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_movements") as batch:
        batch.add_column(sa.Column("reverses_movement_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_inventory_movements_reverses",
            "inventory_movements",
            ["reverses_movement_id"],
            ["id"],
        )
        batch.create_index(
            "ix_inventory_movements_reverses_movement_id", ["reverses_movement_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_movements") as batch:
        batch.drop_index("ix_inventory_movements_reverses_movement_id")
        batch.drop_constraint("fk_inventory_movements_reverses", type_="foreignkey")
        batch.drop_column("reverses_movement_id")
