"""items.allows_negative_price: a discount or concession item (#639)

A negative line is a discount, and until now only the item a QuickBooks
Online discount came in on could carry one (a qbo_mappings row of entity
type discount_item names it). An item of the user's own making now says so
itself: allows_negative_price, off by default, set from the item form. The
data step marks every item a discount mapping names, so the two rules are
one (services/discount_items.py). downgrade drops the column; the mappings
stay, so the older rule still reads.

Revision ID: d2f7a9c4e6b1
Revises: b1a26772cc4e
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2f7a9c4e6b1"
down_revision: Union[str, None] = "b1a26772cc4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMN = "allows_negative_price"


def _flag_mapped_items() -> None:
    """The data step: an item a QuickBooks Online discount came in on takes
    a negative price, as it did through its mapping alone."""
    items = sa.table(
        "items", sa.column("id", sa.Integer), sa.column(COLUMN, sa.Boolean)
    )
    mappings = sa.table(
        "qbo_mappings",
        sa.column("entity_type", sa.String),
        sa.column("slowbooks_id", sa.Integer),
    )
    mapped = sa.select(mappings.c.slowbooks_id).where(
        mappings.c.entity_type == "discount_item"
    )
    op.execute(items.update().where(items.c.id.in_(mapped)).values({COLUMN: True}))


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    if "items" not in tables:
        return
    if COLUMN not in {c["name"] for c in insp.get_columns("items")}:
        op.add_column(
            "items",
            sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "qbo_mappings" in tables:
        _flag_mapped_items()


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "items" not in insp.get_table_names():
        return
    if COLUMN in {c["name"] for c in insp.get_columns("items")}:
        with op.batch_alter_table("items") as batch:
            batch.drop_column(COLUMN)
