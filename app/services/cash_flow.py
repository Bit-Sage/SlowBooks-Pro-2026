"""The statement of cash flows, by the indirect method.

Net income for the period, adjusted for what moved no cash (depreciation, a
gain or loss on selling a fixed asset), plus the change in working capital
(receivables, Undeposited Funds, inventory and other current assets;
payables, credit cards, sales tax, payroll liabilities and other current
liabilities) is cash from operations; fixed and other non-current assets
are investing; loans and other long-term debt, and the owners' money in
and out, are financing. Which is which is the account's detail type
(#390): Fixed Asset and Other Asset, Long Term Liability.

It replaced a statement that classed each cash movement by the type of the
account on its other side: customer receipts routed through Undeposited
Funds (an asset) landed in Investing, bill payments and payroll
withholdings (liabilities) in Financing, and net income never appeared
(exploratory 2.17.3, W-M6 / F18).

Why the sections always add up to the change in cash: every journal entry
balances, so over any set of entries the non-cash lines' credits minus
debits equal the cash lines' debits minus credits. Each non-cash account's
(credit - debit) for the period lands in exactly one place below — the
income statement accounts through net income, every other account in a
section — so the sections sum to the change in cash by construction, not by
a plug.

Cash is the chart's bank accounts (Account.bank_kind == "bank"); a credit
card is a current liability. Opening balances (source_type
"opening_balance") are the books' starting position, not a period's flows:
they are in the beginning cash and nowhere else.
"""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType, DetailType
from app.models.fixed_assets import FixedAssetType
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q

CARRIED_IN = ("opening_balance", "item_opening")
DISPOSAL = "asset_disposal"

_PL_TYPES = (AccountType.INCOME, AccountType.EXPENSE, AccountType.COGS)
# Accumulated depreciation is a Fixed Asset in QuickBooks too (#390), so the
# detail type cannot tell it from the asset it wears down; the register and
# this name rule find it, and it is an adjustment, not investing.
_ACCUMULATED = re.compile(r"\baccum(ulated|\.)?\s*(depr|amort)", re.I)
# Where the detail type puts a balance-sheet account's change (#390). The
# account detail types migration (4ac01efbd72a) gave every account the
# detail type that reproduces where the number ranges and name rules of
# 2.26.0 placed it, so an upgraded company's statement moves no figure.
_NON_CURRENT_ASSETS = {DetailType.FIXED_ASSET.value, DetailType.OTHER_ASSET.value}
_LONG_TERM = {DetailType.LONG_TERM_LIABILITY.value}


def _place(acct: Account, fixed_ids: set, accumulated_ids: set) -> str:
    """Where a non-cash account's change belongs: "net_income",
    "adjustments", "working_capital", "investing" or "financing". By the
    account's detail type: Fixed Asset and Other Asset are investing, Long
    Term Liability financing, every other asset and liability working
    capital. An asset the fixed-asset register depreciates is investing
    whatever its detail type (a type created after the upgrade does not
    retype its account), and accumulated depreciation is an adjustment."""
    kind = acct.account_type
    if kind in _PL_TYPES:
        return "net_income"
    detail = acct.detail_type
    if kind == AccountType.ASSET:
        if acct.id in accumulated_ids or _ACCUMULATED.search(acct.name or ""):
            return "adjustments"
        if acct.id in fixed_ids or detail in _NON_CURRENT_ASSETS:
            return "investing"
        return "working_capital"
    if kind == AccountType.LIABILITY:
        return "financing" if detail in _LONG_TERM else "working_capital"
    return "financing"  # equity: owners' contributions and draws


def _cash_balance(db: Session, cash_ids: list, *filters) -> Decimal:
    if not cash_ids:
        return Decimal("0")
    dr, cr = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id.in_(cash_ids), *filters)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def _row(acct: Account | None, label: str, amount: Decimal, group: str) -> dict:
    return {
        "account_id": acct.id if acct else None,
        "account_name": label,
        "account_number": (acct.account_number or "") if acct else "",
        "amount": float(_q(amount)),
        "group": group,
    }


def statement_of_cash_flows(
    db: Session, start: date, end: date, net_income_label: str = "Net Income"
) -> dict:
    accounts = {a.id: a for a in db.query(Account).all()}
    cash_ids = [a.id for a in accounts.values() if a.bank_kind == "bank"]
    fixed_ids, accumulated_ids = set(), set()
    for t in db.query(FixedAssetType).all():
        if t.asset_account_id:
            fixed_ids.add(t.asset_account_id)
        if t.accumulated_depreciation_account_id:
            accumulated_ids.add(t.accumulated_depreciation_account_id)

    # The period's (credit - debit) per account, disposals apart.
    regular: dict[int, Decimal] = defaultdict(Decimal)
    disposal: dict[int, Decimal] = defaultdict(Decimal)
    for account_id, source_type, dr, cr in (
        db.query(
            TransactionLine.account_id,
            Transaction.source_type,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.date >= start, Transaction.date <= end)
        .filter(func.coalesce(Transaction.source_type, "").notin_(CARRIED_IN))
        .group_by(TransactionLine.account_id, Transaction.source_type)
        .all()
    ):
        flow = Decimal(str(cr)) - Decimal(str(dr))
        (disposal if source_type == DISPOSAL else regular)[account_id] += flow

    net_income = Decimal("0")
    disposal_gain = Decimal("0")  # the P&L side of fixed-asset sales
    disposal_proceeds = Decimal("0")
    sections = {
        "adjustments": [],
        "working_capital": [],
        "investing": [],
        "financing": [],
    }
    order = sorted(
        set(regular) | set(disposal),
        key=lambda i: ((accounts[i].account_number or "~"), accounts[i].name),
    )
    for account_id in order:
        if account_id in cash_ids:
            continue
        acct = accounts[account_id]
        place = _place(acct, fixed_ids, accumulated_ids)
        normal = regular.get(account_id, Decimal("0"))
        sold = disposal.get(account_id, Decimal("0"))
        if place == "net_income":
            net_income += normal + sold
            disposal_gain += sold
            continue
        if place in ("adjustments", "investing"):
            # The cost and accumulated depreciation a sale takes off the
            # books are the sale's proceeds, not depreciation or a purchase.
            disposal_proceeds += sold
            amount = normal
        else:
            amount = normal + sold
        if amount:
            label = (
                f"Depreciation ({acct.name})" if place == "adjustments" else acct.name
            )
            sections[place].append(_row(acct, label, amount, place))

    if disposal_gain:
        # Net income counts the gain or loss; the cash is the proceeds.
        sections["adjustments"].append(
            _row(
                None,
                (
                    "Gain on disposal of fixed assets"
                    if disposal_gain > 0
                    else "Loss on disposal of fixed assets"
                ),
                -disposal_gain,
                "adjustments",
            )
        )
    disposal_proceeds += disposal_gain
    if disposal_proceeds:
        sections["investing"].append(
            _row(
                None,
                "Proceeds from disposal of fixed assets",
                disposal_proceeds,
                "investing",
            )
        )

    def _total(rows):
        return sum((Decimal(str(r["amount"])) for r in rows), Decimal("0"))

    total_operating = (
        _q(net_income)
        + _total(sections["adjustments"])
        + _total(sections["working_capital"])
    )
    total_investing = _total(sections["investing"])
    total_financing = _total(sections["financing"])

    beginning = _cash_balance(db, cash_ids, Transaction.date < start) + _cash_balance(
        db,
        cash_ids,
        Transaction.date >= start,
        Transaction.date <= end,
        Transaction.source_type.in_(CARRIED_IN),
    )
    ending = _cash_balance(db, cash_ids, Transaction.date <= end)

    operating = [_row(None, net_income_label, net_income, "net_income")]
    operating += sections["adjustments"] + sections["working_capital"]
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "net_income": float(_q(net_income)),
        "adjustments": sections["adjustments"],
        "working_capital": sections["working_capital"],
        "operating": operating,
        "investing": sections["investing"],
        "financing": sections["financing"],
        "total_operating": float(total_operating),
        "total_investing": float(total_investing),
        "total_financing": float(total_financing),
        "net_change": float(total_operating + total_investing + total_financing),
        "beginning_cash": float(_q(beginning)),
        "ending_cash": float(_q(ending)),
    }
