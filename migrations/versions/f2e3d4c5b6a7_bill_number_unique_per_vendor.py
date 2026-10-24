"""bills: one bill per vendor invoice number (#501)

Enter Bill refused a vendor's invoice number it already had with a read
(SELECT, then INSERT), which two sign-ins on PostgreSQL can both pass at
once, saving the same number twice. A unique index on (vendor_id,
bill_number) now refuses the second, and the route answers it with the
409 the check gives.

Books may already hold such a pair (the race, or an older import). Before
the index is made, each later copy is renamed — the first keeps its number,
the next becomes "<number> (2)", then "(3)", by id — so the upgrade never
stops on one, and the bills stay as they were otherwise. Books without a
duplicate change nothing but the index.

Revision ID: f2e3d4c5b6a7
Revises: d7e8f9a0b1c2
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2e3d4c5b6a7"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX = "uq_bills_vendor_bill_number"
WIDTH = 100  # bills.bill_number is String(100)


def _renumber_duplicates(bind) -> int:
    rows = bind.execute(
        sa.text(
            "SELECT id, vendor_id, bill_number FROM bills "
            "WHERE (vendor_id, bill_number) IN ("
            "  SELECT vendor_id, bill_number FROM bills "
            "  GROUP BY vendor_id, bill_number HAVING COUNT(*) > 1"
            ") ORDER BY vendor_id, bill_number, id"
        )
    ).fetchall()
    if not rows:
        return 0
    taken = {
        (v, n)
        for v, n in bind.execute(sa.text("SELECT vendor_id, bill_number FROM bills"))
    }
    seen, renamed = set(), 0
    for bill_id, vendor_id, number in rows:
        if (vendor_id, number) not in seen:
            seen.add((vendor_id, number))  # the first keeps its number
            continue
        n = 2
        while True:
            suffix = f" ({n})"
            candidate = number[: WIDTH - len(suffix)] + suffix
            if (vendor_id, candidate) not in taken:
                break
            n += 1
        taken.add((vendor_id, candidate))
        bind.execute(
            sa.text("UPDATE bills SET bill_number = :n WHERE id = :id"),
            {"n": candidate, "id": bill_id},
        )
        renamed += 1
    return renamed


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "bills" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("bills")}:
        return
    _renumber_duplicates(bind)
    op.create_index(INDEX, "bills", ["vendor_id", "bill_number"], unique=True)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bills" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("bills")}:
        op.drop_index(INDEX, table_name="bills")
