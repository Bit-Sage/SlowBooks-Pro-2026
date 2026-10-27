"""pay_applications.tax_rate to six decimals, as estimates store it (#576)

Numeric(7,4) cut 8.875% to 8.88%; Numeric(7,6) keeps 0.08875. Widening only,
so existing values are unchanged. downgrade narrows it back.

Revision ID: c7e1a3b5d9f2
Revises: b4d6f8a0c2e3
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e1a3b5d9f2"
down_revision: Union[str, None] = "b4d6f8a0c2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pay_applications") as batch:
        batch.alter_column(
            "tax_rate",
            existing_type=sa.Numeric(7, 4),
            type_=sa.Numeric(7, 6),
            existing_nullable=False,
            existing_server_default="0",
        )


def downgrade() -> None:
    with op.batch_alter_table("pay_applications") as batch:
        batch.alter_column(
            "tax_rate",
            existing_type=sa.Numeric(7, 6),
            type_=sa.Numeric(7, 4),
            existing_nullable=False,
            existing_server_default="0",
        )
