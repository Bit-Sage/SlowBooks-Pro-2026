"""construction cost-to-complete forecast (#556)

The WIP schedule's estimated total cost is actual + cost to complete, with the
cost to complete entered per job, section and cost type. A cell with no row
defaults to budget - actual (floored at zero), so a company that enters
nothing sees the same figures as a budget-based estimate. Additive: one new
table.

Revision ID: c6e8f0a2d4b6
Revises: b4d6f8a0c2e3
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c6e8f0a2d4b6"
down_revision: Union[str, None] = "b4d6f8a0c2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_cost_to_complete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("cost_code_id", sa.Integer(), nullable=True),
        sa.Column("cost_type", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cost_code_id"], ["cost_codes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "cost_code_id", "cost_type", name="uq_job_cost_to_complete"
        ),
    )
    op.create_index(
        "ix_job_cost_to_complete_job_id",
        "job_cost_to_complete",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_cost_to_complete_id", "job_cost_to_complete", ["id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_job_cost_to_complete_id", table_name="job_cost_to_complete")
    op.drop_index("ix_job_cost_to_complete_job_id", table_name="job_cost_to_complete")
    op.drop_table("job_cost_to_complete")
