# ============================================================================
# Memorized transactions (#605): entering a memorized bill or journal entry
# on its date, the due list, and the one list of every schedule (recurring
# invoices included) the Memorized transactions page shows.
#
# Posting goes through the same code the documents' own routes use:
# _post_bill_lines for a bill (what Enter Bill and a bill edit post) and
# _journal_lines + create_journal_entry for a journal entry, so an entered
# document is the one the form would have made. The schedule itself is the
# recurring engine's: advance_date and roll_next_due_forward in
# recurring_service, with the anchor day a memorized schedule keeps.
# ============================================================================

import logging
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bills import Bill
from app.models.contacts import Vendor
from app.models.invoices import Invoice
from app.models.memorized import (
    KIND_BILL,
    KIND_JOURNAL,
    MODE_AUTO,
    MemorizedTransaction,
)
from app.models.recurring import RecurringInvoice
from app.models.transactions import BASIS_BOTH
from app.schemas.bills import BillLineCreate
from app.schemas.journal import JournalEntryCreate, JournalLineCreate
from app.services.accounting import _q, compute_line_totals, taxed_copy_lines
from app.services.recurring_service import (
    advance_date,
    frequency_label,
    roll_next_due_forward,
)

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_ENDED = "ended"


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


def has_ended(mem: MemorizedTransaction) -> bool:
    """Past its end date, or its last occurrence entered."""
    if mem.end_date and mem.next_date > mem.end_date:
        return True
    if mem.occurrences_limit and (mem.entered_count or 0) >= mem.occurrences_limit:
        return True
    return False


def status_of(mem: MemorizedTransaction) -> str:
    if has_ended(mem):
        return STATUS_ENDED
    return STATUS_ACTIVE if mem.is_active else STATUS_PAUSED


def is_due(mem: MemorizedTransaction, as_of: date) -> bool:
    return status_of(mem) == STATUS_ACTIVE and mem.next_date <= as_of


def advance(mem: MemorizedTransaction) -> None:
    """The schedule's next date after an entry: from the scheduled date,
    whatever day the document was dated."""
    mem.next_date = advance_date(
        mem.next_date, mem.frequency, mem.interval or 1, mem.anchor_day
    )


def roll_forward(mem: MemorizedTransaction, as_of: date) -> None:
    """On resume: the first occurrence on or after as_of, the missed ones
    skipped rather than entered late, as a re-activated recurring invoice
    skips its missed periods."""
    mem.next_date = roll_next_due_forward(
        mem.next_date, mem.frequency, as_of, mem.interval or 1, mem.anchor_day
    )


# ---------------------------------------------------------------------------
# The lines, as the documents' own schemas read them
# ---------------------------------------------------------------------------

_DIMENSIONS = ("job_id", "class_id", "cost_code_id", "cost_type", "function")


def bill_line_of(line) -> BillLineCreate:
    """A stored memorized bill line as Enter Bill's request line, so
    _post_bill_lines prices and posts it exactly as it does a typed one.
    Dimensions are sent only when set, so a blank function still defaults
    from the fund at posting time."""
    fields = {
        "item_id": line.item_id,
        "account_id": line.account_id,
        "description": line.description,
        "quantity": float(line.quantity) if line.quantity is not None else None,
        "rate": float(line.rate) if line.rate is not None else None,
        "is_billable": bool(line.is_billable),
        "line_order": line.line_order or 0,
    }
    for key in _DIMENSIONS:
        value = getattr(line, key, None)
        if value is not None:
            fields[key] = value
    return BillLineCreate(**fields)


def journal_line_of(line) -> JournalLineCreate:
    fields = {
        "account_id": line.account_id,
        "description": line.description or "",
        "debit": Decimal(str(line.debit or 0)),
        "credit": Decimal(str(line.credit or 0)),
        "is_billable": bool(line.is_billable),
    }
    for key in _DIMENSIONS:
        value = getattr(line, key, None)
        if value is not None:
            fields[key] = value
    return JournalLineCreate(**fields)


def bill_total(mem: MemorizedTransaction) -> Decimal:
    lines = [bill_line_of(ln) for ln in mem.lines]
    return compute_line_totals(lines, Decimal(str(mem.tax_rate or 0)))[2]


def journal_total(mem: MemorizedTransaction) -> Decimal:
    return sum((_q(ln.debit or 0) for ln in mem.lines), Decimal("0"))


def amount_of(mem: MemorizedTransaction) -> Decimal:
    return bill_total(mem) if mem.kind == KIND_BILL else journal_total(mem)


# ---------------------------------------------------------------------------
# Entering one
# ---------------------------------------------------------------------------


def _bill_number_for(db: Session, vendor: Vendor, wanted, on: date) -> str:
    """The template's number when it has one, suffixed "-2", "-3"... if that
    vendor already has a bill under it (the same number every month would
    meet the vendor + number index); otherwise the date and the vendor's
    initials, as Enter Bill numbers a bill left blank."""
    from app.routes.bills import _default_bill_number

    base = (wanted or "").strip()
    if not base:
        return _default_bill_number(db, vendor, on)
    taken = {
        row[0]
        for row in db.query(Bill.bill_number)
        .filter(Bill.vendor_id == vendor.id, Bill.bill_number.like(f"{base}%"))
        .all()
    }
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _enter_bill(db: Session, mem: MemorizedTransaction, on: date) -> Bill:
    from app.routes.bills import _post_bill_lines
    from app.routes.invoices.helpers import _due_date_from_terms
    from app.services.currency import resolve_rate

    vendor = db.query(Vendor).filter(Vendor.id == mem.vendor_id).first()
    if not vendor:
        raise HTTPException(
            status_code=404,
            detail=f"The vendor of the memorized bill {mem.name!r} no longer exists.",
        )
    lines = [bill_line_of(ln) for ln in mem.lines]
    tax_rate = Decimal(str(mem.tax_rate or 0))
    subtotal, tax_amount, total = compute_line_totals(lines, tax_rate)
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The memorized bill {mem.name!r} adds up to $0.00, so nothing "
                "was entered. Open it and enter an amount on at least one line."
            ),
        )
    terms = (mem.terms or "").strip() or vendor.terms or "Net 30"
    _, doc_rate = resolve_rate(db, None, None)
    bill = Bill(
        bill_number=_bill_number_for(db, vendor, mem.bill_number, on),
        vendor_id=vendor.id,
        date=on,
        due_date=_due_date_from_terms(on, terms),
        terms=terms,
        subtotal=subtotal,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        class_id=mem.class_id,
        job_id=mem.job_id,
        notes=mem.notes,
    )
    db.add(bill)
    db.flush()
    _post_bill_lines(db, bill, vendor, lines, tax_amount, total, doc_rate, on)
    return bill


def _enter_journal(db: Session, mem: MemorizedTransaction, on: date):
    from app.routes.journal import _journal_lines
    from app.services.accounting import create_journal_entry

    data = JournalEntryCreate(
        date=on,
        description=mem.description or mem.name,
        reference=mem.reference,
        class_id=mem.class_id,
        job_id=mem.job_id,
        is_adjusting=bool(mem.is_adjusting),
        basis=mem.basis,
        lines=[journal_line_of(ln) for ln in mem.lines],
    )
    lines = _journal_lines(db, data, basis=data.basis or BASIS_BOTH)
    if len(lines) < 2:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The memorized journal entry {mem.name!r} has fewer than two "
                "lines with an amount, so nothing was entered."
            ),
        )
    try:
        return create_journal_entry(
            db,
            on,
            data.description,
            lines,
            source_type="manual",
            reference=data.reference or "",
            class_id=data.class_id,
            job_id=data.job_id,
            is_adjusting=bool(data.is_adjusting),
            posted_by=db.info.get("acting_username") or "operator",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def enter_memorized(
    db: Session, mem: MemorizedTransaction, on: date | None = None
) -> tuple[str, int]:
    """Post the document dated `on` (the scheduled date when not given) and
    advance the schedule. Returns (kind, the bill's or transaction's id).
    A closed period, a missing vendor or a $0.00 template is an
    HTTPException, as the document's own route would answer; nothing is
    written when one is raised."""
    from app.services.accountants_copy import check_entry_date
    from app.services.closing_date import check_closing_date

    on = on or mem.next_date
    if has_ended(mem):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{mem.name!r} has ended: "
                + (
                    f"its end date was {mem.end_date.isoformat()}."
                    if mem.end_date and mem.next_date > mem.end_date
                    else f"all {mem.occurrences_limit} occurrences are entered."
                )
                + " Edit it and move the end to enter it again."
            ),
        )
    check_closing_date(db, on)
    check_entry_date(db, on)

    if mem.kind == KIND_BILL:
        bill = _enter_bill(db, mem, on)
        document_id = bill.id
    elif mem.kind == KIND_JOURNAL:
        txn = _enter_journal(db, mem, on)
        document_id = txn.id
    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown memorized transaction kind {mem.kind!r}"
        )

    mem.entered_count = (mem.entered_count or 0) + 1
    mem.last_entered_on = on
    mem.last_document_id = document_id
    advance(mem)
    db.flush()
    return mem.kind, document_id


def enter_due_memorized(
    db: Session, as_of: date | None = None, skipped: list | None = None
) -> list[dict]:
    """The scheduler's run: every auto-enter schedule due on or before
    as_of is entered once, dated its scheduled date. A remind schedule is
    never entered here; it waits on the due list. One that cannot be
    entered (a closed period, a vendor gone, a $0.00 template) is skipped
    and reported in `skipped`, and the rest go through."""
    if as_of is None:
        as_of = date.today()
    due = (
        db.query(MemorizedTransaction)
        .filter(
            MemorizedTransaction.mode == MODE_AUTO,
            MemorizedTransaction.is_active,
            MemorizedTransaction.next_date <= as_of,
        )
        .order_by(MemorizedTransaction.next_date, MemorizedTransaction.id)
        .all()
    )
    entered: list[dict] = []
    for mem in due:
        if has_ended(mem):
            continue
        on = mem.next_date
        # A SAVEPOINT per schedule: a refusal part way through one (an
        # inventory item with no asset account, say) rolls back only that
        # one's rows, and the others in the run still go through.
        nested = db.begin_nested()
        try:
            kind, document_id = enter_memorized(db, mem, on)
            nested.commit()
        except HTTPException as e:
            nested.rollback()
            if skipped is not None:
                skipped.append(
                    {
                        "memorized_id": mem.id,
                        "name": mem.name,
                        "kind": mem.kind,
                        "message": (
                            f"{mem.name} (due {on.isoformat()}) was not entered: "
                            f"{e.detail}"
                        ),
                    }
                )
            continue
        entered.append(
            {
                "memorized_id": mem.id,
                "name": mem.name,
                "kind": kind,
                "document_id": document_id,
                "date": on.isoformat(),
                "next_date": mem.next_date.isoformat(),
            }
        )
    db.commit()
    return entered


# ---------------------------------------------------------------------------
# What a memorized transaction still points at
# ---------------------------------------------------------------------------


def names_using(db: Session, column: str, value: int) -> list[str]:
    """The memorized transactions whose header or lines carry `value` in
    `column` (class_id, job_id, cost_code_id...), by name: a class, job or
    cost code is refused deletion while one does, as it is while a posted
    line does, rather than failing on the foreign key."""
    from app.models.memorized import MemorizedTransactionLine

    names: list[str] = []
    if hasattr(MemorizedTransaction, column):
        for (name,) in (
            db.query(MemorizedTransaction.name)
            .filter(getattr(MemorizedTransaction, column) == value)
            .all()
        ):
            names.append(name)
    if hasattr(MemorizedTransactionLine, column):
        for (name,) in (
            db.query(MemorizedTransaction.name)
            .join(
                MemorizedTransactionLine,
                MemorizedTransactionLine.memorized_id == MemorizedTransaction.id,
            )
            .filter(getattr(MemorizedTransactionLine, column) == value)
            .all()
        ):
            names.append(name)
    return sorted(set(names))


def refuse_if_memorized(db: Session, what: str, column: str, value: int) -> None:
    names = names_using(db, column, value)
    if names:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{what} is used by the memorized transaction"
                f"{'s' if len(names) > 1 else ''} {', '.join(names)}. Edit or "
                "delete those first, or mark it inactive instead."
            ),
        )


# ---------------------------------------------------------------------------
# Every schedule in one list
# ---------------------------------------------------------------------------


def row_of(mem: MemorizedTransaction, as_of: date) -> dict:
    return {
        "kind": mem.kind,
        "id": mem.id,
        "name": mem.name,
        "party": mem.vendor.name if mem.kind == KIND_BILL and mem.vendor else None,
        "mode": mem.mode,
        "frequency": mem.frequency,
        "interval": mem.interval or 1,
        "frequency_label": frequency_label(mem.frequency, mem.interval or 1),
        "next_date": mem.next_date,
        "end_date": mem.end_date,
        "occurrences": mem.occurrences_limit,
        "entered_count": mem.entered_count or 0,
        "status": status_of(mem),
        "is_due": is_due(mem, as_of),
        "last_entered_on": mem.last_entered_on,
        "last_document_id": mem.last_document_id,
        "amount": amount_of(mem),
    }


def _invoice_rows(db: Session, as_of: date) -> list[dict]:
    """Recurring invoices as rows of the same shape: always auto-enter
    (the generator posts them), the frequency words their own, the last
    entry read off the invoices they made."""
    from app.services.settings_service import is_nonprofit

    recs = db.query(RecurringInvoice).order_by(RecurringInvoice.next_due).all()
    if not recs:
        return []
    last = dict(
        db.query(Invoice.recurring_invoice_id, func.max(Invoice.date))
        .filter(Invoice.recurring_invoice_id.isnot(None))
        .group_by(Invoice.recurring_invoice_id)
        .all()
    )
    last_id = {}
    for rec_id, when in last.items():
        row = (
            db.query(Invoice.id)
            .filter(Invoice.recurring_invoice_id == rec_id, Invoice.date == when)
            .order_by(Invoice.id.desc())
            .first()
        )
        last_id[rec_id] = row[0] if row else None
    kind_word = "pledge" if is_nonprofit(db) else "invoice"
    rows = []
    for rec in recs:
        ended = bool(rec.end_date and rec.next_due > rec.end_date)
        if ended:
            status = STATUS_ENDED
        elif rec.is_active:
            status = STATUS_ACTIVE
        else:
            status = STATUS_PAUSED
        copied = taxed_copy_lines(rec.lines, rec.customer)
        total = compute_line_totals(copied, Decimal(str(rec.tax_rate or 0)))[2]
        rows.append(
            {
                "kind": "invoice",
                "id": rec.id,
                "name": (
                    f"Recurring {kind_word}: {rec.customer.name}"
                    if rec.customer
                    else f"Recurring {kind_word} #{rec.id}"
                ),
                "party": rec.customer.name if rec.customer else None,
                "mode": MODE_AUTO,
                "frequency": rec.frequency,
                "interval": 1,
                "frequency_label": frequency_label(rec.frequency, 1),
                "next_date": rec.next_due,
                "end_date": rec.end_date,
                "occurrences": None,
                "entered_count": rec.invoices_created or 0,
                "status": status,
                "is_due": status == STATUS_ACTIVE and rec.next_due <= as_of,
                "last_entered_on": last.get(rec.id),
                "last_document_id": last_id.get(rec.id),
                "amount": total,
            }
        )
    return rows


def schedule_rows(
    db: Session,
    as_of: date | None = None,
    due_only: bool = False,
    kind: str | None = None,
) -> list[dict]:
    """Every schedule (recurring invoices, memorized bills, memorized
    journal entries) as one list, soonest first; `due_only` keeps the ones
    due on or before as_of, `kind` one kind."""
    if as_of is None:
        as_of = date.today()
    rows: list[dict] = []
    if kind in (None, "invoice"):
        rows.extend(_invoice_rows(db, as_of))
    if kind in (None, KIND_BILL, KIND_JOURNAL):
        q = db.query(MemorizedTransaction)
        if kind:
            q = q.filter(MemorizedTransaction.kind == kind)
        rows.extend(row_of(m, as_of) for m in q.all())
    if due_only:
        rows = [r for r in rows if r["is_due"]]
    rows.sort(key=lambda r: (r["next_date"], r["name"].lower(), r["id"]))
    return rows
