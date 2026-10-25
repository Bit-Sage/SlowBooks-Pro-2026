# ============================================================================
# Estimates — "Convert to Invoice" deep-copies every field and line item,
# then marks the estimate CONVERTED. PDFs are rendered with WeasyPrint.
# ============================================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes.invoices.helpers import (
    _due_date_from_terms,
    _post_invoice_journal,
    confirm_credit_limit,
    confirm_zero_total,
    opening_status,
    resolve_line_taxable,
)
from app.routes._helpers import clamp_pagination, reject_null_updates
from app.routes.invoice_messages import default_message_body
from app.services.audit import audited_bulk_delete
from app.services.job_costing import line_cost_type, budgets_from_estimate
from app.services.estimating import estimate_totals, reprice_factor
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.models.invoices import Invoice, InvoiceLine
from app.models.contacts import Customer
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.schemas.estimates import (
    EstimateCreate,
    EstimateUpdate,
    EstimateResponse,
    EstimateAccept,
)
from app.schemas.invoices import InvoiceResponse, ZeroTotalConfirmation
from app.services.pdf_service import generate_estimate_pdf
from app.services.numbering import next_estimate_number, next_invoice_number
from app.services.settings_service import get_all_settings as get_settings, set_setting
from app.services.request_utils import content_disposition
from app.services.accounting import (
    _q,
    compute_line_totals,
)
from app.services.jobs_service import (
    own_job,
    refuse_other_customers_jobs,
    refuse_other_customers_jobs_on_edit,
)

router = APIRouter(prefix="/api/estimates", tags=["estimates"])


_ADDRESS_PARTS = ("address1", "address2", "city", "state", "zip")


def _customer_bill_to(customer) -> dict:
    return {f"bill_{k}": getattr(customer, f"bill_{k}", None) for k in _ADDRESS_PARTS}


def _estimate_response(est: Estimate) -> EstimateResponse:
    """A single estimate's response: the customer name, each line's package
    name, and the bid math (#551). The list endpoint skips the totals."""
    resp = EstimateResponse.model_validate(est)
    if est.customer:
        resp.customer_name = est.customer.name
    by_line = {ln.id: ln for ln in est.lines}
    for line_resp in resp.lines:
        line = by_line.get(line_resp.id)
        if line is not None and line.trade_package is not None:
            line_resp.trade_package_name = line.trade_package.name
    resp.totals = estimate_totals(est)
    return resp


def _accept_estimate(db: Session, estimate: Estimate, job_id: Optional[int]):
    """Accept an estimate (#551): lock its job's budget to it. Shared by the
    POST /accept endpoint and a PUT that sets status=accepted, so there is one
    code path. The budget is seeded (manual rows win) and the estimate's own
    rows are marked locked; from then on only a change order moves them."""
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=409, detail="Estimate already converted")
    if estimate.status == EstimateStatus.ACCEPTED:
        raise HTTPException(status_code=409, detail="Estimate already accepted")
    job_id = job_id if job_id is not None else estimate.job_id
    if job_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "no_job", "message": "Accepting needs a job to budget."},
        )
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.customer_id != estimate.customer_id:
        raise HTTPException(
            status_code=422, detail="Job belongs to a different customer"
        )
    # A budget already locked by a DIFFERENT estimate stays its contract.
    other_lock = (
        db.query(JobBudget)
        .filter(
            JobBudget.job_id == job.id,
            JobBudget.locked.is_(True),
            JobBudget.estimate_id != estimate.id,
        )
        .first()
    )
    if other_lock is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "budget_locked",
                "message": "This job's budget is already locked to another estimate.",
            },
        )

    estimate.job_id = job.id
    estimate.status = EstimateStatus.ACCEPTED
    estimate.accepted_at = datetime.now(timezone.utc)
    budgets_from_estimate(db, job, estimate)
    # Lock exactly the rows this acceptance seeded; manual rows stay editable.
    db.query(JobBudget).filter(
        JobBudget.job_id == job.id,
        JobBudget.source == "estimate",
        JobBudget.estimate_id == estimate.id,
    ).update({JobBudget.locked: True}, synchronize_session=False)


@router.get("", response_model=list[EstimateResponse])
def list_estimates(
    status: str = None,
    customer_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load .customer and .lines to avoid N+1 during model_validate.
    q = db.query(Estimate).options(
        joinedload(Estimate.customer),
        selectinload(Estimate.lines),
    )
    if status:
        q = q.filter(Estimate.status == status)
    if customer_id:
        q = q.filter(Estimate.customer_id == customer_id)
    estimates = (
        q.order_by(Estimate.date.desc(), Estimate.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    results = []
    for est in estimates:
        resp = EstimateResponse.model_validate(est)
        if est.customer:
            resp.customer_name = est.customer.name
        results.append(resp)
    return results


@router.get("/{estimate_id}", response_model=EstimateResponse)
def get_estimate(estimate_id: int, db: Session = Depends(get_db)):
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    return _estimate_response(est)


@router.post("", response_model=EstimateResponse, status_code=201)
def create_estimate(data: EstimateCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    # The job, on the header or a line, is this customer's (NEW-36).
    refuse_other_customers_jobs(db, customer.id, data.job_id, data.lines, "estimate")

    cust_id = customer.id
    # The estimate is addressed to the customer, as an invoice is; it was
    # never filled, so its PDF and the invoice it became had no address.
    bill_to = _customer_bill_to(customer)
    resolve_line_taxable(db, data.lines, customer)
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    estimate = None
    estimate_number = None
    last_err = None
    # Same race as create_invoice: next_estimate_number's check-then-insert
    # window lets two concurrent creates pick the same number. Retry on
    # IntegrityError; the UNIQUE constraint is the safety net.
    for _ in range(10):
        estimate_number = next_estimate_number(db)
        estimate = Estimate(
            estimate_number=estimate_number,
            customer_id=cust_id,
            date=data.date,
            expiration_date=data.expiration_date,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            gc_pct=data.gc_pct,
            insurance_pct=data.insurance_pct,
            fee_pct=data.fee_pct,
            bond_pct=data.bond_pct,
            **bill_to,
        )
        db.add(estimate)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "estimate_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            estimate = None
    if estimate is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique estimate number; please retry.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = EstimateLine(
            estimate_id=estimate.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            class_name=line_data.class_name,
            job_id=line_data.job_id,
            cost_code_id=line_data.cost_code_id,
            cost_type=line_cost_type(db, line_data.cost_code_id, line_data.cost_type),
            unit_cost=line_data.unit_cost,
            trade_package_id=line_data.trade_package_id,
            line_kind=line_data.line_kind,
            unit=line_data.unit,
            alternate_accepted=line_data.alternate_accepted,
            is_taxable=(
                line_data.is_taxable if line_data.is_taxable is not None else True
            ),
            line_order=line_data.line_order or i,
        )
        db.add(line)

    numeric_part = estimate_number.removeprefix(
        get_settings(db).get("estimate_prefix", "E-")
    )
    if numeric_part.isdigit():
        set_setting(db, "estimate_next_number", str(int(numeric_part) + 1))

    db.commit()
    db.refresh(estimate)
    return _estimate_response(estimate)


@router.put("/{estimate_id}", response_model=EstimateResponse)
def update_estimate(
    estimate_id: int, data: EstimateUpdate, db: Session = Depends(get_db)
):
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")

    changes = reject_null_updates(
        Estimate, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    # Accepting through a PUT takes the one accept code path, after the other
    # changes (a new job on the same request) land (#551).
    accepting = changes.get("status") == EstimateStatus.ACCEPTED
    if accepting:
        changes.pop("status")
    if changes.get("customer_id") not in (None, estimate.customer_id):
        customer = db.get(Customer, changes["customer_id"])
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        # a different customer, so their address
        changes.update(_customer_bill_to(customer))
    # What the edit changes — the customer, the header's job, a line's job —
    # must leave the estimate on its customer's jobs (NEW-36); what it
    # leaves alone is not judged.
    refuse_other_customers_jobs_on_edit(db, estimate, changes, data.lines, "estimate")
    for key, val in changes.items():
        setattr(estimate, key, val)

    if data.lines is not None:
        # Each line's tax is settled before it is stored, the way create does
        # it: a non-taxable customer's lines are never taxable, and a line
        # sent without a flag takes its item's. The flags used to be stored
        # as sent (a missing one as taxable) and settled only for the
        # totals, so an edit stored taxable lines on an exempt customer's
        # estimate, and a non-taxable item's line stored as taxable was
        # taxed on the invoice the estimate became (found integrating the
        # 2.17.3 exploratory fixes). The customer is the one the estimate
        # has after this edit.
        resolve_line_taxable(db, data.lines, db.get(Customer, estimate.customer_id))
        # each old line leaves a DELETE event with its amounts (#289)
        audited_bulk_delete(
            db, db.query(EstimateLine).filter(EstimateLine.estimate_id == estimate_id)
        )
        for i, line_data in enumerate(data.lines):
            line = EstimateLine(
                estimate_id=estimate_id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=_q(
                    Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
                ),
                class_name=line_data.class_name,
                job_id=line_data.job_id,
                cost_code_id=line_data.cost_code_id,
                cost_type=line_cost_type(
                    db, line_data.cost_code_id, line_data.cost_type
                ),
                unit_cost=line_data.unit_cost,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=line_data.line_order or i,
            )
            db.add(line)

        tax_rate = data.tax_rate if data.tax_rate is not None else estimate.tax_rate
        subtotal, tax_amount, total = compute_line_totals(data.lines, tax_rate)
        estimate.subtotal = subtotal
        estimate.tax_amount = tax_amount
        estimate.total = total
    elif "tax_rate" in changes or "customer_id" in changes:
        # A new rate or a new customer without resending the lines: the
        # stored lines are re-totalled (a rate-only edit left the old tax and
        # total in place), and a non-taxable customer's exemption covers them.
        stored = list(estimate.lines)
        if "customer_id" in changes:
            resolve_line_taxable(db, stored, db.get(Customer, estimate.customer_id))
        subtotal, tax_amount, total = compute_line_totals(stored, estimate.tax_rate)
        estimate.subtotal = subtotal
        estimate.tax_amount = tax_amount
        estimate.total = total

    if accepting:
        db.flush()
        _accept_estimate(db, estimate, estimate.job_id)

    db.commit()
    db.refresh(estimate)
    return _estimate_response(estimate)


@router.post("/{estimate_id}/reprice", response_model=EstimateResponse)
def reprice_from_cost(estimate_id: int, db: Session = Depends(get_db)):
    """Price from cost (#551): set each base / accepted-alternate line's rate
    to its unit cost times the markup factor (1+gc)(1+ins)(1+fee)(1+bond), to
    the cent, and recompute the line and document totals the way an edit
    does. Lines without a unit cost keep their rate; allowances and unit-price
    lines are never repriced. Refused once the estimate is accepted or
    converted — its numbers are settled then."""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status in (EstimateStatus.ACCEPTED, EstimateStatus.CONVERTED):
        raise HTTPException(
            status_code=409,
            detail="A settled estimate can't be repriced; make a new one or a change order.",
        )

    factor = reprice_factor(estimate)
    for line in estimate.lines:
        kind = line.line_kind or "base"
        base_scope = kind == "base" or (kind == "alternate" and line.alternate_accepted)
        if not base_scope or line.unit_cost is None:
            continue
        line.rate = _q(Decimal(str(line.unit_cost)) * factor)
        line.amount = _q(Decimal(str(line.quantity or 0)) * Decimal(str(line.rate)))

    subtotal, tax_amount, total = compute_line_totals(
        list(estimate.lines), estimate.tax_rate
    )
    estimate.subtotal = subtotal
    estimate.tax_amount = tax_amount
    estimate.total = total

    db.commit()
    db.refresh(estimate)
    return _estimate_response(estimate)


@router.post("/{estimate_id}/accept", response_model=EstimateResponse)
def accept_estimate(
    estimate_id: int,
    data: Optional[EstimateAccept] = None,
    db: Session = Depends(get_db),
):
    """Accept the estimate and lock its job's budget to it (#551). Needs a
    job (on the body or the estimate); refuses an already accepted/converted
    estimate, or a job whose budget another estimate already locked."""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    _accept_estimate(db, estimate, data.job_id if data else None)
    db.commit()
    db.refresh(estimate)
    return _estimate_response(estimate)


@router.get("/{estimate_id}/pdf")
def estimate_pdf(estimate_id: int, db: Session = Depends(get_db)):
    """Generate the estimate PDF."""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    pdf_bytes = generate_estimate_pdf(est, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Estimate_{est.estimate_number}.pdf"
            )
        },
    )


@router.get("/{estimate_id}/print-preview")
def estimate_print_preview(estimate_id: int, db: Session = Depends(get_db)):
    """Render estimate as HTML page for browser print dialog (window.print())"""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    from fastapi.responses import HTMLResponse

    from app.services.pdf_service import _render

    # The PDF's own renderer: one set of filters and helpers for both.
    html_str = _render("estimate_pdf.html", company, est=est)
    html_str = html_str.replace(
        "</body>", "<script>window.onload=function(){window.print();}</script></body>"
    )
    return HTMLResponse(content=html_str)


@router.post("/{estimate_id}/convert", response_model=InvoiceResponse)
def convert_to_invoice(
    estimate_id: int,
    data: Optional[ZeroTotalConfirmation] = None,
    db: Session = Depends(get_db),
):
    """Convert to invoice — deep-copies all fields and lines."""
    from app.services.closing_date import check_closing_date

    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=400, detail="Estimate already converted")
    # The invoice is dated the day it is made, as QuickBooks does: keeping
    # the estimate's date back-dated the sale and its due date (2.17.3
    # exploratory W-L10). Its journal posts on that date, so that is the
    # date the closing lock is checked against.
    today = date.today()
    check_closing_date(db, today)

    # A new invoice gets the customer's CURRENT tax treatment, computed —
    # not the estimate's stored tax (see taxed_copy_lines).
    from app.services.accounting import compute_line_totals, taxed_copy_lines

    # The bid's own lines only: unit prices are a rate card and an alternate
    # becomes scope only once accepted, so neither converts (#551).
    def _converts(line) -> bool:
        kind = line.line_kind or "base"
        if kind == "unit_price":
            return False
        if kind == "alternate" and not line.alternate_accepted:
            return False
        return True

    kept = [ln for ln in estimate.lines if _converts(ln)]
    copied = taxed_copy_lines(kept, estimate.customer)
    subtotal, tax_amount, total = compute_line_totals(copied, estimate.tax_rate)
    confirm_zero_total(
        total,
        bool(data and data.allow_zero_total),
        "estimate",
        "converting it",
        question="This estimate adds up to $0.00. Convert it to an invoice anyway?",
    )
    from app.services.contact_balances import home_amount

    confirm_credit_limit(
        db,
        estimate.customer,
        home_amount(total, getattr(estimate, "exchange_rate", None)),
        bool(data and data.allow_over_credit_limit),
        "invoice",
        question=None,
    )

    invoice_number = next_invoice_number(db)

    # Terms and due date as a new invoice for this customer gets them: the
    # customer's terms, else the company's (the form does the same).
    settings = get_settings(db)
    customer = estimate.customer
    terms = (customer.terms if customer else None) or settings.get(
        "default_terms", "Net 30"
    )
    due_date = _due_date_from_terms(today, terms)

    # The bill-to is the estimate's, else the customer's, and the ship-to
    # the customer's; the notes are the estimate's, else the company's
    # default invoice notes. The converted invoice printed with no address
    # and without the default notes (2.17.3 exploratory F21).
    address = {f"bill_{k}": getattr(estimate, f"bill_{k}") for k in _ADDRESS_PARTS}
    if customer and not any(address.values()):
        address = _customer_bill_to(customer)
    if customer:
        address.update(
            {f"ship_{k}": getattr(customer, f"ship_{k}") for k in _ADDRESS_PARTS}
        )

    # The copy carries the customer's jobs only; another customer's job
    # stored on the estimate (books from before the rule) is left off,
    # and the response says which (NEW-36).
    left_off: list[str] = []
    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=estimate.customer_id,
        status=opening_status(total),
        date=today,
        due_date=due_date,
        terms=terms,
        subtotal=subtotal,
        tax_rate=estimate.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        class_id=estimate.class_id,
        job_id=own_job(db, estimate.customer_id, estimate.job_id, left_off),
        # the estimate's own notes; else what a new invoice starts with (#392)
        notes=estimate.notes
        or default_message_body(db)
        or settings.get("invoice_notes")
        or None,
        **address,
    )
    db.add(invoice)
    db.flush()

    new_lines = []
    for eline, cline in zip(kept, copied):
        iline = InvoiceLine(
            invoice_id=invoice.id,
            item_id=eline.item_id,
            description=eline.description,
            quantity=eline.quantity,
            rate=eline.rate,
            amount=eline.amount,
            class_name=eline.class_name,
            job_id=own_job(db, estimate.customer_id, eline.job_id, left_off),
            cost_code_id=eline.cost_code_id,
            # the cost basis the estimate carried, kept on the invoice (#551)
            cost_type=eline.cost_type,
            unit_cost=eline.unit_cost,
            is_taxable=cline.is_taxable,
            line_order=eline.line_order,
        )
        db.add(iline)
        new_lines.append(iline)

    estimate.status = EstimateStatus.CONVERTED
    estimate.converted_invoice_id = invoice.id

    # The posting a new invoice gets — DR A/R, CR income per line with the
    # line's job, class and cost code, CR sales tax — through the same
    # path as create and duplicate. The route's own builder posted the
    # header's job only, so a converted estimate's line-level jobs counted
    # under "No job" in P&L by Job (2.22.0 gate, found fixing NEW-36).
    txn = _post_invoice_journal(
        db, invoice, new_lines, customer.name if customer else ""
    )
    # A $0.00 conversion posts no journal, so it keeps no transaction.
    invoice.transaction_id = txn.id if txn is not None else None

    # Phase 11 (audit fix): estimate→invoice conversion is a NEW sale from
    # an accounting standpoint. Post inventory movements for each inventory
    # line so the ledger stays consistent with the A/R journal entry.
    db.flush()
    db.refresh(invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, invoice, txn_date=today)

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    resp.jobs_left_off = left_off
    return resp
