"""possible duplicates: groups kept as "not a duplicate" (issue #414)

The Possible Duplicates report lists entries on a bank or card account with
the same amount on the same side and the same or a similar payee a few days
apart. Two equal monthly payments are legitimate, so a group can be
dismissed, and it stays dismissed for everyone on the company: one row per
account and group (its sorted transaction ids).

Revision ID: b7d2e9a41c63
Revises: d3e4f5a6b7c8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7d2e9a41c63"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "duplicate_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False
        ),
        # The id list, for display and the subset match. Text, not a bounded
        # String: it never keys anything, so its length cannot 500 a write.
        sa.Column("transaction_ids", sa.Text(), nullable=False),
        # The per-account key: a fixed-length sha256 of the sorted ids, so a
        # long group's key never overflows the column.
        sa.Column("group_hash", sa.String(length=64), nullable=False),
        sa.Column("dismissed_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "account_id", "group_hash", name="uq_duplicate_dismissals_group"
        ),
    )
    op.create_index("ix_duplicate_dismissals_id", "duplicate_dismissals", ["id"])
    op.create_index(
        "ix_duplicate_dismissals_account_id", "duplicate_dismissals", ["account_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_duplicate_dismissals_account_id", table_name="duplicate_dismissals"
    )
    op.drop_index("ix_duplicate_dismissals_id", table_name="duplicate_dismissals")
    op.drop_table("duplicate_dismissals")
