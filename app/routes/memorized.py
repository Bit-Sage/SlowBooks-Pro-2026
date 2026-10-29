# ============================================================================
# Memorized transactions (#605): memorized bills and journal entries, and
# the one list of every schedule the Memorized transactions page shows.
#
# /api/memorized/{id} is a memorized bill or journal entry. The list rows
# carry a kind: "invoice" rows are recurring invoices, kept under
# /api/recurring (their own CRUD, pause through PUT is_active, Enter now
# through POST /api/recurring/{id}/generate), so that API is unchanged.
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.models.memorized import (
    KIND_BILL,
    KIND_JOURNAL,
    MemorizedTransaction,
    MemorizedTransactionLine,
)
from app.models.transactions import BASIS_BOTH
from app.routes._helpers import reject_null_updates
from app.schemas.journal import JournalEntryCreate
from app.schemas.memorized import (
    EnterRequest,
    EnterResponse,
    MemorizedCreate,
    MemorizedResponse,
    MemorizedUpdate,
    ScheduleRow,
)
from app.services.accounting import compute_line_totals
from app.services.memorized_service import (
    amount_of,
    enter_due_memorized,
    enter_memorized,
    frequency_label,
    is_due,
    roll_forward,
    schedule_rows,
    status_of,
)

router = APIRouter(prefix="/api/memorized", tags=["memorized"])

_DOC_WORD = {KIND_BILL: "bill", KIND_JOURNAL: "journal entry"}


def _get(db: Session, mem_id: int) -> MemorizedTransaction:
    mem = (
        db.query(MemorizedTransaction).filter(MemorizedTransaction.id == mem_id).first()
    )
    if not mem:
        raise HTTPException(status_code=404, detail="Memorized transaction not found")
    return mem


def _response(
    mem: MemorizedTransaction, as_of: date | None = None
) -> MemorizedResponse:
    as_of = as_of or date.today()
    resp = MemorizedResponse.model_validate(mem)
    resp.occurrences = mem.occurrences_limit
    resp.frequency_label = frequency_label(mem.frequency, mem.interval or 1)
    resp.status = status_of(mem)
    resp.is_due = is_due(mem, as_of)
    resp.vendor_name = mem.vendor.name if mem.vendor else None
    resp.amount = amount_of(mem)
    for line, out in zip(mem.lines, resp.lines):
        if mem.kind == KIND_BILL and line.quantity is not None:
            out.amount = compute_line_totals([line], Decimal("0"))[0]
    return resp


def _check_with_books(db: Session, data: MemorizedCreate) -> None:
    """What the schema cannot know: the vendor exists, a bill adds up to
    more than $0.00 (a template that did not made $0.00 documents, as
    recurring invoices once did), a journal entry's accounts and cost
    types exist."""
    if data.kind == KIND_BILL:
        if not db.query(Vendor).filter(Vendor.id == data.vendor_id).first():
            raise HTTPException(status_code=404, detail="Vendor not found")
        lines = [ln.as_bill_line() for ln in data.lines]
        if compute_line_totals(lines, data.tax_rate)[2] <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A memorized bill must be for more than zero. Enter the "
                    "amount the vendor charges, or a quantity and rate, on at "
                    "least one line."
                ),
            )
    else:
        from app.routes.journal import _journal_lines

        entry = JournalEntryCreate(
            date=data.start_date,
            description=data.description,
            lines=[ln.as_journal_line() for ln in data.lines],
        )
        _journal_lines(db, entry, basis=data.basis or BASIS_BOTH)


def _write_lines(db: Session, mem: MemorizedTransaction, lines) -> None:
    db.query(MemorizedTransactionLine).filter(
        MemorizedTransactionLine.memorized_id == mem.id
    ).delete()
    for i, line in enumerate(lines):
        db.add(
            MemorizedTransactionLine(
                memorized_id=mem.id,
                line_order=i,
                account_id=line.account_id,
                description=line.description,
                job_id=line.job_id,
                class_id=line.class_id,
                cost_code_id=line.cost_code_id,
                cost_type=line.cost_type,
                function=line.function,
                is_billable=bool(line.is_billable),
                item_id=line.item_id if mem.kind == KIND_BILL else None,
                quantity=line.quantity if mem.kind == KIND_BILL else None,
                rate=line.rate if mem.kind == KIND_BILL else None,
                debit=line.debit if mem.kind == KIND_JOURNAL else None,
                credit=line.credit if mem.kind == KIND_JOURNAL else None,
            )
        )


_HEADER_FIELDS = (
    "name",
    "mode",
    "frequency",
    "interval",
    "end_date",
    "vendor_id",
    "bill_number",
    "terms",
    "tax_rate",
    "notes",
    "description",
    "reference",
    "is_adjusting",
    "basis",
    "class_id",
    "job_id",
)


@router.get("", response_model=list[ScheduleRow])
def list_schedules(
    due: bool = False,
    kind: str | None = Query(default=None),
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Every schedule in one list: recurring invoices (kind "invoice"),
    memorized bills and memorized journal entries. `due=true` keeps the
    ones due on or before today (or `as_of`)."""
    if kind is not None and kind not in ("invoice", KIND_BILL, KIND_JOURNAL):
        raise HTTPException(
            status_code=422, detail="kind must be invoice, bill or journal"
        )
    return schedule_rows(db, as_of, due_only=due, kind=kind)


@router.get("/due", response_model=list[ScheduleRow])
def list_due(as_of: date | None = Query(default=None), db: Session = Depends(get_db)):
    """The schedules due on or before today: a remind schedule waiting to
    be entered, and an auto-enter one the scheduler has not run for yet."""
    return schedule_rows(db, as_of, due_only=True)


@router.post("/enter-due")
def enter_due(as_of: date | None = Query(default=None), db: Session = Depends(get_db)):
    """What the scheduler does: enter every auto-enter memorized bill and
    journal entry due on or before as_of (today), one entry each, dated
    its scheduled date. Remind schedules are left on the due list."""
    skipped: list = []
    entered = enter_due_memorized(db, as_of, skipped=skipped)
    return {"entered": entered, "entered_count": len(entered), "skipped": skipped}


@router.get("/{mem_id}", response_model=MemorizedResponse)
def get_memorized(mem_id: int, db: Session = Depends(get_db)):
    return _response(_get(db, mem_id))


@router.post("", response_model=MemorizedResponse, status_code=201)
def create_memorized(data: MemorizedCreate, db: Session = Depends(get_db)):
    _check_with_books(db, data)
    mem = MemorizedTransaction(
        kind=data.kind,
        name=data.name,
        mode=data.mode,
        frequency=data.frequency,
        interval=data.interval,
        start_date=data.start_date,
        next_date=data.start_date,
        anchor_day=data.start_date.day,
        end_date=data.end_date,
        occurrences_limit=data.occurrences,
        vendor_id=data.vendor_id if data.kind == KIND_BILL else None,
        bill_number=(data.bill_number or "").strip() or None,
        terms=data.terms,
        tax_rate=data.tax_rate,
        notes=data.notes,
        description=data.description,
        reference=data.reference,
        is_adjusting=bool(data.is_adjusting),
        basis=data.basis,
        class_id=data.class_id,
        job_id=data.job_id,
    )
    db.add(mem)
    db.flush()
    _write_lines(db, mem, data.lines)
    db.commit()
    db.refresh(mem)
    return _response(mem)


@router.put("/{mem_id}", response_model=MemorizedResponse)
def update_memorized(mem_id: int, data: MemorizedUpdate, db: Session = Depends(get_db)):
    mem = _get(db, mem_id)
    changes = reject_null_updates(
        MemorizedTransaction, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    # The whole, as it will be, checked by the create rules: a change of
    # frequency, vendor, end date or lines meets the same refusals a new
    # one does.
    whole = {key: getattr(mem, key) for key in _HEADER_FIELDS}
    whole["kind"] = mem.kind
    whole["tax_rate"] = float(mem.tax_rate or 0)
    whole["occurrences"] = mem.occurrences_limit
    # the end date is judged against the first date, as at creation, so an
    # ended schedule can still be edited
    whole["start_date"] = mem.start_date
    if data.lines is None:
        whole["lines"] = [
            {
                "account_id": ln.account_id,
                "description": ln.description,
                "job_id": ln.job_id,
                "class_id": ln.class_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
                "function": ln.function,
                "is_billable": bool(ln.is_billable),
                "line_order": ln.line_order,
                "item_id": ln.item_id,
                "quantity": None if ln.quantity is None else float(ln.quantity),
                "rate": None if ln.rate is None else float(ln.rate),
                "debit": ln.debit or 0,
                "credit": ln.credit or 0,
            }
            for ln in mem.lines
        ]
    else:
        whole["lines"] = [ln.model_dump(exclude_unset=True) for ln in data.lines]
    for key, value in changes.items():
        if key == "next_date":
            continue
        whole[key] = value
    try:
        checked = MemorizedCreate(**whole)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _check_with_books(db, checked)

    for key in _HEADER_FIELDS:
        if key in changes:
            setattr(mem, key, getattr(checked, key))
    mem.frequency, mem.interval = checked.frequency, checked.interval
    if "bill_number" in changes:
        mem.bill_number = (checked.bill_number or "").strip() or None
    if "occurrences" in changes:
        mem.occurrences_limit = checked.occurrences
    if "next_date" in changes:
        mem.next_date = changes["next_date"]
        mem.anchor_day = mem.next_date.day
    if data.lines is not None:
        _write_lines(db, mem, checked.lines)
    db.commit()
    db.refresh(mem)
    return _response(mem)


@router.delete("/{mem_id}")
def delete_memorized(mem_id: int, db: Session = Depends(get_db)):
    """The schedule goes; the bills and journal entries it entered stay,
    as documents of their own."""
    mem = _get(db, mem_id)
    db.delete(mem)
    db.commit()
    return {"message": "Memorized transaction deleted"}


@router.post("/{mem_id}/enter", response_model=EnterResponse)
def enter_now(
    mem_id: int, data: EnterRequest | None = None, db: Session = Depends(get_db)
):
    """Enter the document now, dated its scheduled date (or `date`), and
    move the schedule on. Works for a remind schedule, and for an
    auto-enter one ahead of the scheduler."""
    mem = _get(db, mem_id)
    on = data.date if data and data.date else mem.next_date
    kind, document_id = enter_memorized(db, mem, on)
    db.commit()
    db.refresh(mem)
    word = _DOC_WORD.get(kind, kind)
    return EnterResponse(
        kind=kind,
        document_id=document_id,
        date=on,
        next_date=mem.next_date,
        status=status_of(mem),
        message=(
            f"Entered the {word} dated {on.isoformat()}; "
            + (
                f"next on {mem.next_date.isoformat()}."
                if status_of(mem) != "ended"
                else "that was the last one, so the schedule has ended."
            )
        ),
    )


@router.post("/{mem_id}/pause", response_model=MemorizedResponse)
def pause_memorized(mem_id: int, db: Session = Depends(get_db)):
    mem = _get(db, mem_id)
    mem.is_active = False
    db.commit()
    db.refresh(mem)
    return _response(mem)


@router.post("/{mem_id}/resume", response_model=MemorizedResponse)
def resume_memorized(
    mem_id: int, as_of: date | None = Query(default=None), db: Session = Depends(get_db)
):
    """Resume: the next date rolls forward to the first occurrence on or
    after today, so the months it sat paused are skipped, not entered late
    (as a re-activated recurring invoice skips its missed periods)."""
    mem = _get(db, mem_id)
    mem.is_active = True
    roll_forward(mem, as_of or date.today())
    db.commit()
    db.refresh(mem)
    return _response(mem)
