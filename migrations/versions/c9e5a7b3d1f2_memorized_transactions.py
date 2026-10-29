"""memorized transactions (#605)

Two tables for a bill or a journal entry kept whole with a schedule:
memorized_transactions (the header, the schedule and its mode) and
memorized_transaction_lines (a bill line's item, account, quantity and rate
or a journal line's debit and credit, with the memo, job, class, cost code
and function either carries). Recurring invoices keep their own tables.
Nothing is backfilled: there was no earlier representation. downgrade drops
both tables.

Revision ID: c9e5a7b3d1f2
Revises: b8d4f2a6c1e3
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9e5a7b3d1f2"
down_revision: Union[str, None] = "b8d4f2a6c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memorized_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("next_date", sa.Date(), nullable=False),
        sa.Column("anchor_day", sa.Integer(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("occurrences_limit", sa.Integer(), nullable=True),
        sa.Column("entered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_entered_on", sa.Date(), nullable=True),
        sa.Column("last_document_id", sa.Integer(), nullable=True),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("bill_number", sa.String(length=100), nullable=True),
        sa.Column("terms", sa.String(length=50), nullable=True),
        sa.Column("tax_rate", sa.Numeric(7, 6), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column(
            "is_adjusting", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("basis", sa.String(length=10), nullable=True),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendors.id"], name="fk_memorized_transactions_vendor_id"
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"], name="fk_memorized_transactions_class_id"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_memorized_transactions_job_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memorized_transactions_id", "memorized_transactions", ["id"], unique=False
    )
    op.create_index(
        "ix_memorized_transactions_kind",
        "memorized_transactions",
        ["kind"],
        unique=False,
    )
    op.create_index(
        "ix_memorized_transactions_next_date",
        "memorized_transactions",
        ["next_date"],
        unique=False,
    )
    op.create_index(
        "ix_memorized_transactions_vendor_id",
        "memorized_transactions",
        ["vendor_id"],
        unique=False,
    )

    op.create_table(
        "memorized_transaction_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("memorized_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("cost_code_id", sa.Integer(), nullable=True),
        sa.Column("cost_type", sa.String(length=20), nullable=True),
        sa.Column("function", sa.String(length=20), nullable=True),
        sa.Column(
            "is_billable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("rate", sa.Numeric(17, 4), nullable=True),
        sa.Column("debit", sa.Numeric(15, 2), nullable=True),
        sa.Column("credit", sa.Numeric(15, 2), nullable=True),
        sa.ForeignKeyConstraint(
            ["memorized_id"],
            ["memorized_transactions.id"],
            name="fk_memorized_transaction_lines_memorized_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_memorized_transaction_lines_account_id",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_memorized_transaction_lines_job_id"
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name="fk_memorized_transaction_lines_class_id",
        ),
        sa.ForeignKeyConstraint(
            ["cost_code_id"],
            ["cost_codes.id"],
            name="fk_memorized_transaction_lines_cost_code_id",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name="fk_memorized_transaction_lines_item_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memorized_transaction_lines_id",
        "memorized_transaction_lines",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_memorized_transaction_lines_memorized_id",
        "memorized_transaction_lines",
        ["memorized_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memorized_transaction_lines_memorized_id",
        table_name="memorized_transaction_lines",
    )
    op.drop_index(
        "ix_memorized_transaction_lines_id", table_name="memorized_transaction_lines"
    )
    op.drop_table("memorized_transaction_lines")
    op.drop_index(
        "ix_memorized_transactions_vendor_id", table_name="memorized_transactions"
    )
    op.drop_index(
        "ix_memorized_transactions_next_date", table_name="memorized_transactions"
    )
    op.drop_index("ix_memorized_transactions_kind", table_name="memorized_transactions")
    op.drop_index("ix_memorized_transactions_id", table_name="memorized_transactions")
    op.drop_table("memorized_transactions")
