# ============================================================================
# Recurring Invoices — CRUD + manual generate
# Feature 2: Schedule automatic invoice generation
# ============================================================================

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import reject_null_updates
from app.routes.invoices.helpers import refuse_zero_total, resolve_line_taxable
from app.models.invoices import Invoice
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.contacts import Customer
from app.schemas.recurring import RecurringCreate, RecurringUpdate, RecurringResponse
from app.services.accounting import compute_line_totals
from app.services.jobs_service import (
    refuse_other_customers_jobs,
    refuse_other_customers_jobs_on_edit,
)
from app.services.recurring_service import (
    generate_due_invoices,
    roll_next_due_forward,
)
from app.services.terminology import terms_from_db

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("", response_model=list[RecurringResponse])
def list_recurring(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(RecurringInvoice)
    if active_only:
        q = q.filter(RecurringInvoice.is_active)
    recs = q.order_by(RecurringInvoice.next_due).all()
    results = []
    for r in recs:
        resp = RecurringResponse.model_validate(r)
        if r.customer:
            resp.customer_name = r.customer.name
        results.append(resp)
    return results


@router.get("/{rec_id}", response_model=RecurringResponse)
def get_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


def _refuse_end_before_start(start: date, end: date | None) -> None:
    """A schedule ending before it starts was accepted (2.17.3 exploratory
    W-L4); it could never bill anything."""
    if end is not None and end < start:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The end date ({end:%b} {end.day}, {end.year}) is before the start "
                f"date ({start:%b} {start.day}, {start.year}). Pick an end date on "
                "or after the start date, or leave it blank for no end."
            ),
        )


def _schedule_noun(db: Session) -> str:
    """The schedule as the company's vocabulary names it: "recurring
    invoice", or "recurring pledge" for a nonprofit."""
    return "recurring " + terms_from_db(db)("invoice")


@router.post("", response_model=RecurringResponse, status_code=201)
def create_recurring(data: RecurringCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    _refuse_end_before_start(data.start_date, data.end_date)
    # The job is this customer's (NEW-36); the schedule's lines carry none.
    refuse_other_customers_jobs(db, customer.id, data.job_id, (), _schedule_noun(db))

    # Refused here rather than skipped at every run: a template that adds up
    # to nothing generated $0.00 invoices that then showed as overdue.
    resolve_line_taxable(db, data.lines, customer)
    refuse_zero_total(
        compute_line_totals(data.lines, data.tax_rate)[2], _schedule_noun(db)
    )

    rec = RecurringInvoice(
        customer_id=data.customer_id,
        frequency=data.frequency,
        start_date=data.start_date,
        end_date=data.end_date,
        next_due=data.start_date,
        terms=data.terms,
        tax_rate=data.tax_rate,
        notes=data.notes,
        class_id=data.class_id,
        job_id=data.job_id,
    )
    db.add(rec)
    db.flush()

    for i, line_data in enumerate(data.lines):
        db.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=line_data.line_order or i,
            )
        )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    resp.customer_name = customer.name
    return resp


@router.put("/{rec_id}", response_model=RecurringResponse)
def update_recurring(rec_id: int, data: RecurringUpdate, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    if "end_date" in data.model_fields_set:
        _refuse_end_before_start(rec.start_date, data.end_date)
    changes = reject_null_updates(
        RecurringInvoice, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    # A changed job must be the schedule's customer's (NEW-36); a job sent
    # unchanged, or left alone, is not judged (the lines carry none).
    refuse_other_customers_jobs_on_edit(db, rec, changes, None, _schedule_noun(db))

    if data.lines is not None:
        resolve_line_taxable(db, data.lines, rec.customer)
        tax_rate = data.tax_rate if data.tax_rate is not None else rec.tax_rate
        refuse_zero_total(
            compute_line_totals(data.lines, tax_rate)[2], _schedule_noun(db)
        )

    was_active = rec.is_active
    for key, val in changes.items():
        setattr(rec, key, val)

    # Re-activating a paused template resumed from a stale next_due and
    # back-dated an invoice for every missed period (and a date inside the
    # closing date made generate refuse every schedule). When Active goes
    # from off to on, roll next_due forward by whole periods to the first
    # occurrence on or after today; missed periods are skipped, not billed.
    if changes.get("is_active") and not was_active:
        rec.next_due = roll_next_due_forward(rec.next_due, rec.frequency, date.today())

    if data.lines is not None:
        db.query(RecurringInvoiceLine).filter(
            RecurringInvoiceLine.recurring_invoice_id == rec_id
        ).delete()
        for i, line_data in enumerate(data.lines):
            db.add(
                RecurringInvoiceLine(
                    recurring_invoice_id=rec_id,
                    item_id=line_data.item_id,
                    description=line_data.description,
                    quantity=line_data.quantity,
                    rate=line_data.rate,
                    is_taxable=(
                        line_data.is_taxable
                        if line_data.is_taxable is not None
                        else True
                    ),
                    line_order=line_data.line_order or i,
                )
            )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


@router.delete("/{rec_id}")
def delete_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    # A template that has made invoices cannot be deleted: deleting it left
    # those invoices pointing at a template that no longer existed
    # (SlowBooks-Pro-Testing#101). It keeps billing until the user acts.
    made = db.query(Invoice).filter(Invoice.recurring_invoice_id == rec.id).count()
    if made:
        t = terms_from_db(db)
        noun = t("invoice") if made == 1 else t("invoices")
        raise HTTPException(
            status_code=409,
            detail=(
                f"This {_schedule_noun(db)} has made {made} {noun}, so it "
                "cannot be deleted, and it keeps billing until you act. Set an "
                "End Date to stop it for good, or clear Active to pause it."
            ),
        )
    db.delete(rec)
    db.commit()
    return {"message": "Recurring invoice deleted"}


@router.post("/generate")
def generate_now(as_of: date = Query(default=None), db: Session = Depends(get_db)):
    """Manually trigger generation of all due recurring invoices — one
    installment per template per call. `as_of` (default today) lets a
    catch-up or a test run generate a past installment deterministically."""
    skipped: list = []
    created_ids = generate_due_invoices(db, as_of, skipped=skipped)
    return {
        "invoices_created": len(created_ids),
        "invoice_ids": created_ids,
        "skipped": skipped,
    }
