"""Profit & Loss Detail (#477): every posted line under each income, cost
of goods and expense account for a period, in the P&L's order.

The lines are the drill-down's (bank_register.account_register): the same
scope for a class and a job (bank_register.line_scope), the same payee,
reference, void mark and link to the document; one query across the P&L's
accounts instead of one per account. Each account carries its own running
balance from zero (a P&L account's balance is the period's) and its total;
the totals add up to the Profit & Loss for the same dates and filters, which
tests/test_pl_detail.py holds.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.services.bank_register import (
    DEBIT_NORMAL,
    line_scope,
    payees_for,
    references_for,
    source_link,
    source_word,
    voided_transaction_ids,
)
from app.services.entry_text import header_text

PL_TYPES = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
PL_SECTIONS = ("income", "cogs", "expenses", "other_income", "other_expense")
SPLIT_WORD = "-SPLIT-"  # QuickBooks' word for a transaction across several accounts
ZERO = Decimal("0")
_CHUNK = 500


def _splits(db: Session, txn_ids, chart: dict):
    """{(transaction id, account id): the Split column}: the one other
    account on the transaction, by its number and path, or -SPLIT- when
    there are several, as QuickBooks writes it."""
    from app.routes.reports.financial import _account_words

    ids = sorted(txn_ids)
    on: dict[int, set] = defaultdict(set)
    for i in range(0, len(ids), _CHUNK):
        for txn_id, acct_id in db.query(
            TransactionLine.transaction_id, TransactionLine.account_id
        ).filter(TransactionLine.transaction_id.in_(ids[i : i + _CHUNK])):
            on[txn_id].add(acct_id)

    def split(txn_id, acct_id):
        others = on.get(txn_id, set()) - {acct_id}
        if not others:
            return ""
        if len(others) > 1:
            return SPLIT_WORD
        other = next(iter(others))
        return _account_words(chart, other)[1] or ""

    return split


def pl_detail_lines(
    db: Session,
    start: date,
    end: date,
    *,
    class_id: int | None = None,
    job_id: int | None = None,
    exclude_adjusting: bool = False,
    account_ids: set | None = None,
    chart: dict | None = None,
    words=None,
) -> dict:
    """{account id: block} for every P&L account with a line in the period
    (or only those in `account_ids`), each block {account_id, number,
    name, path, label, section, entries, amount, debit, credit}. An entry
    is {line_id, transaction_id, date, type, source_type, num, name, memo,
    split, debit, credit, amount, balance, source_link, voided}; `amount`
    is in the account's natural sign, as the P&L adds it, and `balance` the
    running total from the period's start."""
    from app.routes.reports.financial import (
        _account_words,
        _chart_index,
        _pl_section_of,
    )

    chart = chart if chart is not None else _chart_index(db)
    q = (
        db.query(TransactionLine, Transaction, Account.account_type)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type.in_(PL_TYPES),
            Transaction.date >= start,
            Transaction.date <= end,
        )
    )
    scope = line_scope(db, class_id, job_id)
    if scope is not None:
        q = q.filter(scope)
    if exclude_adjusting:
        q = q.filter(Transaction.is_adjusting == False)  # noqa: E712
    if account_ids is not None:
        if not account_ids:
            return {}
        q = q.filter(TransactionLine.account_id.in_(sorted(account_ids)))
    rows = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()

    txns = {txn.id: txn for _, txn, _ in rows}
    voided = voided_transaction_ids(db, txns.keys())
    payees = payees_for(db, txns.values())
    refs = references_for(db, list(txns.values()))
    split = _splits(db, txns.keys(), chart)

    blocks: dict[int, dict] = {}
    for tl, txn, acct_type in rows:
        aid = tl.account_id
        block = blocks.get(aid)
        if block is None:
            number, name = chart[aid][0], chart[aid][1]
            path, label = _account_words(chart, aid)
            block = blocks[aid] = {
                "account_id": aid,
                "account_number": number,
                "account_name": name,
                "account_path": path or name,
                "account_label": label or name,
                "section": _pl_section_of(chart[aid][3], chart[aid][4]),
                "entries": [],
                "_amount": ZERO,
                "_debit": ZERO,
                "_credit": ZERO,
            }
        dr, cr = tl.debit or ZERO, tl.credit or ZERO
        natural = (dr - cr) if acct_type in DEBIT_NORMAL else (cr - dr)
        block["_amount"] += natural
        block["_debit"] += dr
        block["_credit"] += cr
        link = source_link(txn) or f"/#/journal/{txn.id}"
        block["entries"].append(
            {
                "line_id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "type": source_word(txn.source_type, words),
                "source_type": txn.source_type or "journal",
                "num": refs.get(txn.id, ""),
                "name": payees.get(txn.id, ""),
                "memo": tl.description or header_text(txn, tl.description),
                "split": split(txn.id, aid),
                "debit": float(dr),
                "credit": float(cr),
                "amount": float(natural),
                "balance": float(block["_amount"]),
                "source_link": link,
                "voided": txn.id in voided
                or bool((txn.source_type or "").endswith("_void")),
            }
        )
    for block in blocks.values():
        block["amount"] = float(block.pop("_amount"))
        block["debit"] = float(block.pop("_debit"))
        block["credit"] = float(block.pop("_credit"))
    return blocks
