"""account detail types (#390): accounts.detail_type

QuickBooks' fifteen account types refine the six base types: Bank,
Accounts Receivable, Other Current Asset, Fixed Asset, Other Asset; Accounts
Payable, Credit Card, Other Current Liability, Long Term Liability; Equity;
Income, Other Income; Cost of Goods Sold; Expense, Other Expense. A CPA
reading the Balance Sheet expects current and long-term apart, and the
chart could not say which is which.

The column is added nullable, backfilled, then made NOT NULL. Nothing in
this release prints by detail type yet (the statements follow in the next
one), so the backfill decides only the heading a line will sit under. It
reproduces where the cash flow statement places each account today
(app/services/cash_flow.py `_place`), so the statements that later read
the detail type move no figure between working capital, investing and
financing:

1. A bank or card account (bank_kind) is Bank / Credit Card.
2. The fifteen control accounts take what the posting code expects there
   and the API then locks: 1100 Accounts Receivable, 2000 Accounts
   Payable, Bank / Credit Card with a bank kind, else their type's
   default.
3. An asset is a Fixed Asset when the fixed-asset register names it (as an
   asset or an accumulated-depreciation account), when it is accumulated
   depreciation by name, when it is numbered 1500-1999 and named like
   equipment, or when it is named like equipment and carries no four-digit
   1xxx number; numbered 1500-1999 otherwise it is an Other Asset; numbered
   1000-1499 it is an Other Current Asset whatever its name.
4. A liability named like long-term debt (loan, note payable, mortgage,
   line of credit, ...) is a Long Term Liability: the seeded 2400 Loan
   Payable among them.
5. 4900 Other Income is Other Income; 6999 Exchange Gain/Loss and 7999
   Gain/Loss on Asset Disposal are Other Expense (number and the name they
   are created with, so a renumbered or renamed account keeps the default).
6. Everything else takes its type's default. A sub-account that took the
   default shares its parent's detail type where the base types agree and
   the cash flow would place it where it did (walking down from the top):
   a default-typed asset or liability is working capital, so it does not
   take a Fixed Asset, Other Asset or Long Term Liability parent's type
   (Shop Shelving under 1500 Equipment, Accrued Interest under 2400 Loan
   Payable stay current). One the rules above placed (a bank account under
   an asset folder, a numbered asset under a fixed-asset parent), or one
   kept current that way, keeps its own and is named in the migration log;
   the user can line it up on the chart page.

The name rules are copied here, not imported, so this revision does the
same thing however cash_flow.py changes later.

Revision ID: 4ac01efbd72a
Revises: d3e4f5a6b7c8
Create Date: 2026-10-23
"""

import logging
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4ac01efbd72a"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# cash_flow.py, 2.26.0
_ACCUMULATED = re.compile(r"\baccum(ulated|\.)?\s*(depr|amort)", re.I)
_FIXED_ASSET = re.compile(
    r"\b(equipment|vehicles?|furniture|fixtures?|buildings?|land|machinery|"
    r"leasehold|property|computers?|fixed assets?|intangible|goodwill)\b",
    re.I,
)
_LONG_TERM_DEBT = re.compile(
    r"\b(loans?|notes? payable|mortgages?|long[- ]term|line of credit|"
    r"lease liabilit(y|ies)|bonds? payable)\b",
    re.I,
)

# The control accounts the posting code finds by number
# (app/services/control_accounts.py, 2.26.0): never re-parented here.
_CONTROL = {
    "1000",
    "1100",
    "1200",
    "1300",
    "2000",
    "2100",
    "2200",
    "3200",
    "4000",
    "4800",
    "5000",
    "5900",
    "6000",
    "6120",
    "6150",
}
# (number, name as created) -> detail type
_NAMED = {
    ("4900", "other income"): "other_income",
    ("6999", "exchange gain/loss"): "other_expense",
    ("7999", "gain/loss on asset disposal"): "other_expense",
}
_DEFAULT = {
    "ASSET": "other_current_asset",
    "LIABILITY": "other_current_liability",
    "EQUITY": "equity",
    "INCOME": "income",
    "COGS": "cost_of_goods_sold",
    "EXPENSE": "expense",
}
_BASE = {
    "bank": "ASSET",
    "accounts_receivable": "ASSET",
    "other_current_asset": "ASSET",
    "fixed_asset": "ASSET",
    "other_asset": "ASSET",
    "accounts_payable": "LIABILITY",
    "credit_card": "LIABILITY",
    "other_current_liability": "LIABILITY",
    "long_term_liability": "LIABILITY",
    "equity": "EQUITY",
    "income": "INCOME",
    "other_income": "INCOME",
    "cost_of_goods_sold": "COGS",
    "expense": "EXPENSE",
    "other_expense": "EXPENSE",
}
# Where the cash flow puts a balance-sheet account outside working capital
# (investing, financing) once it reads the detail type.
_NON_CURRENT = {"fixed_asset", "other_asset", "long_term_liability"}
# Pinned to a bank kind or a control number: never handed down to a child.
_PINNED = {"bank", "credit_card", "accounts_receivable", "accounts_payable"}


def _type_name(value) -> str:
    """account_type is stored as the enum NAME on every engine ('ASSET')."""
    return str(value or "").upper()


def infer(row: dict, fixed_ids: set, accumulated_ids: set) -> tuple[str, bool]:
    """(detail type, placed) for one account; placed=False means it took
    its type's default and may share its parent's instead."""
    atype = _type_name(row["account_type"])
    name = " ".join((row["name"] or "").split())
    number = (row["account_number"] or "").strip()
    kind = row["bank_kind"]
    named = _NAMED.get((number or None, name.casefold())) or next(
        (d for (n, nm), d in _NAMED.items() if not number and nm == name.casefold()),
        None,
    )
    if number in _CONTROL:
        # what the posting code expects there, and what the API locks
        if atype == "ASSET" and kind == "bank":
            return "bank", True
        if atype == "LIABILITY" and kind == "credit_card":
            return "credit_card", True
        if (number, atype) == ("1100", "ASSET"):
            return "accounts_receivable", True
        if (number, atype) == ("2000", "LIABILITY"):
            return "accounts_payable", True
        return _DEFAULT.get(atype, "other_current_asset"), True
    if atype == "ASSET":
        if kind == "bank":
            return "bank", True
        if row["id"] in accumulated_ids or _ACCUMULATED.search(name):
            return "fixed_asset", True
        if row["id"] in fixed_ids:
            return "fixed_asset", True
        if number.isdigit() and len(number) == 4 and number.startswith("1"):
            if number < "1500":
                return "other_current_asset", True
            return ("fixed_asset" if _FIXED_ASSET.search(name) else "other_asset"), True
        if _FIXED_ASSET.search(name):
            return "fixed_asset", True
    elif atype == "LIABILITY":
        if kind == "credit_card":
            return "credit_card", True
        if _LONG_TERM_DEBT.search(name):
            return "long_term_liability", True
    elif named and _BASE[named] == atype:
        return named, True
    return _DEFAULT.get(atype, "other_current_asset"), False


def backfill(rows: list[dict], fixed_ids: set, accumulated_ids: set):
    """{id: detail type} for every account, and the sub-accounts left with a
    detail type other than their parent's (for the log)."""
    by_id = {r["id"]: r for r in rows}
    result: dict[int, str] = {}
    placed: dict[int, bool] = {}
    for r in rows:
        result[r["id"]], placed[r["id"]] = infer(r, fixed_ids, accumulated_ids)

    def depth(r) -> int:
        seen, n, node = set(), 0, r
        while node["parent_id"] in by_id and node["id"] not in seen:
            seen.add(node["id"])
            node = by_id[node["parent_id"]]
            n += 1
        return n

    unmatched = []
    for r in sorted(rows, key=depth):  # parents settle before children
        parent = by_id.get(r["parent_id"])
        if parent is None or parent["id"] == r["id"]:
            continue
        want = result[parent["id"]]
        if result[r["id"]] == want:
            continue
        if (
            not placed[r["id"]]
            and want not in _PINNED
            and _BASE[want] == _type_name(r["account_type"])
            # the cash flow placed a default-typed asset or liability in
            # working capital; it takes a non-current parent's type only if
            # that keeps it there, so no figure moves (#390 review)
            and (want in _NON_CURRENT) == (result[r["id"]] in _NON_CURRENT)
        ):
            result[r["id"]] = want
        else:
            unmatched.append((r, parent))
    return result, unmatched


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "accounts" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("accounts")}
    if "detail_type" not in present:
        with op.batch_alter_table("accounts") as batch:
            batch.add_column(sa.Column("detail_type", sa.String(30), nullable=True))

    fixed_ids, accumulated_ids = set(), set()
    if "fixed_asset_types" in insp.get_table_names():
        for asset_id, accum_id in bind.execute(
            sa.text(
                "SELECT asset_account_id, accumulated_depreciation_account_id "
                "FROM fixed_asset_types"
            )
        ):
            if asset_id:
                fixed_ids.add(asset_id)
            if accum_id:
                accumulated_ids.add(accum_id)

    rows = [
        dict(r._mapping)
        for r in bind.execute(
            sa.text(
                "SELECT id, name, account_number, account_type, bank_kind, "
                "parent_id, detail_type FROM accounts"
            )
        )
    ]
    result, unmatched = backfill(rows, fixed_ids, accumulated_ids)
    todo = [{"id": r["id"], "d": result[r["id"]]} for r in rows if not r["detail_type"]]
    if todo:
        bind.execute(
            sa.text("UPDATE accounts SET detail_type = :d WHERE id = :id"), todo
        )
    for child, parent in unmatched:
        log.info(
            "Account %s %s keeps detail type %s under %s %s (%s)",
            child["account_number"] or "",
            child["name"],
            result[child["id"]],
            parent["account_number"] or "",
            parent["name"],
            result[parent["id"]],
        )

    # server_default so a raw SQL insert that omits the column can't fail the
    # NOT NULL (the ORM hook still writes the type-correct value for app rows).
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column(
            "detail_type",
            existing_type=sa.String(30),
            nullable=False,
            server_default="other_current_asset",
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "accounts" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("accounts")}
    if "detail_type" in present:
        with op.batch_alter_table("accounts") as batch:
            batch.drop_column("detail_type")
