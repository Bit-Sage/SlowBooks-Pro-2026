"""credit_memo_lines gains class_id and job_id (#640)

A credit memo's lines take their own class and job, as invoice lines do.
NULL falls back to the memo's header, which is what every line had before,
so nothing is backfilled. downgrade drops both columns.

Revision ID: b1a26772cc4e
Revises: b8d4f2a6c1e3
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1a26772cc4e"
down_revision: Union[str, None] = "b8d4f2a6c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("credit_memo_lines") as batch:
        batch.add_column(sa.Column("class_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("job_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_credit_memo_lines_class_id", "classes", ["class_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_credit_memo_lines_job_id", "jobs", ["job_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("credit_memo_lines") as batch:
        batch.drop_constraint("fk_credit_memo_lines_job_id", type_="foreignkey")
        batch.drop_constraint("fk_credit_memo_lines_class_id", type_="foreignkey")
        batch.drop_column("job_id")
        batch.drop_column("class_id")
