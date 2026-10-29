# ============================================================================
# Lien waivers (#555) — conditional / unconditional, progress / final, one per
# payment, job and type. The record holds the part of the payment applied to
# the job and the through date; GET /{id}/pdf prints it.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.lien_waivers import LienWaiver
from app.models.pay_applications import PayApplication
from app.models.payments import Payment, PaymentAllocation
from app.schemas.lien_waivers import LienWaiverCreate, LienWaiverOut
from app.services.request_utils import content_disposition

router = APIRouter(prefix="/api/lien-waivers", tags=["lien-waivers"])


def _get(db: Session, waiver_id: int) -> LienWaiver:
    waiver = db.get(LienWaiver, waiver_id)
    if waiver is None:
        raise HTTPException(status_code=404, detail="Lien waiver not found")
    return waiver


@router.get("", response_model=list[LienWaiverOut])
def list_lien_waivers(
    payment_id: int | None = Query(None),
    job_id: int | None = Query(None),
    pay_application_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(LienWaiver)
    if payment_id is not None:
        q = q.filter(LienWaiver.payment_id == payment_id)
    if job_id is not None:
        q = q.filter(LienWaiver.job_id == job_id)
    if pay_application_id is not None:
        q = q.filter(LienWaiver.pay_application_id == pay_application_id)
    return q.order_by(LienWaiver.id).all()


def _waiver_job(db: Session, payment: Payment, job_id: int | None) -> int | None:
    """The job a waiver on this payment is for. The payment's invoices name
    the jobs it pays: one job (invoices with no job alongside it do not
    count) is taken as read; more than one needs `job_id` to pick; a payment
    on no job's invoices has no job. A `job_id` the payment does not pay is
    refused."""
    job_ids = {
        row[0]
        for row in db.query(Invoice.job_id)
        .join(PaymentAllocation, PaymentAllocation.invoice_id == Invoice.id)
        .filter(PaymentAllocation.payment_id == payment.id)
        .distinct()
    }
    jobs = sorted(j for j in job_ids if j is not None)
    if job_id is not None:
        if job_id not in jobs:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "job_not_paid",
                    "message": "This payment pays nothing on that job.",
                    "job_ids": jobs,
                },
            )
        return job_id
    if len(jobs) <= 1:
        return jobs[0] if jobs else None
    names = {
        j.id: j.name for j in db.query(Job).filter(Job.id.in_(jobs)).order_by(Job.id)
    }
    raise HTTPException(
        status_code=400,
        detail={
            "code": "job_required",
            "message": "This payment pays more than one job; say which job the "
            "waiver is for: " + ", ".join(names.get(j, str(j)) for j in jobs) + ".",
            "job_ids": jobs,
        },
    )


@router.post("", response_model=LienWaiverOut, status_code=201)
def create_lien_waiver(data: LienWaiverCreate, db: Session = Depends(get_db)):
    payment = db.get(Payment, data.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(
            status_code=409, detail="A voided payment can't have a lien waiver."
        )
    # A waiver is for one payment and one job (#576): a payment split across
    # two jobs' invoices gets one per job, each for the part applied there.
    job_id = _waiver_job(db, payment, data.job_id)
    taken = (
        db.query(LienWaiver.id)
        .filter(
            LienWaiver.payment_id == payment.id,
            LienWaiver.job_id == job_id,
            LienWaiver.waiver_type == data.waiver_type,
        )
        .first()
    )
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail="This payment already has that kind of lien waiver for the job.",
        )
    # The payment application comes from the job's invoice the payment pays;
    # the amount is what the payment applied to that job's invoices (the whole
    # payment when it carries no job).
    on_job = (
        db.query(Invoice)
        .join(PaymentAllocation, PaymentAllocation.invoice_id == Invoice.id)
        .filter(PaymentAllocation.payment_id == payment.id, Invoice.job_id == job_id)
        .order_by(Invoice.id)
    )
    invoice = on_job.filter(Invoice.pay_application_id.isnot(None)).first()
    app = db.get(PayApplication, invoice.pay_application_id) if invoice else None
    if job_id is None:
        amount = Decimal(str(payment.amount))
    else:
        amount = Decimal(
            str(
                db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
                .filter(
                    PaymentAllocation.payment_id == payment.id,
                    Invoice.job_id == job_id,
                )
                .scalar()
            )
        )
    waiver = LienWaiver(
        payment_id=payment.id,
        job_id=job_id,
        pay_application_id=app.id if app else None,
        waiver_type=data.waiver_type,
        amount=amount,
        through_date=(
            data.through_date
            or (app.period_to if app and app.period_to else None)
            or payment.date
        ),
        signed_by=data.signed_by,
        notes=data.notes,
    )
    db.add(waiver)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This payment already has that kind of lien waiver for the job.",
        )
    db.refresh(waiver)
    return waiver


@router.get("/{waiver_id}/pdf")
def lien_waiver_pdf(waiver_id: int, db: Session = Depends(get_db)):
    from app.services.pdf_service import generate_lien_waiver_pdf
    from app.services.settings_service import get_all_settings

    waiver = _get(db, waiver_id)
    job = db.get(Job, waiver.job_id) if waiver.job_id else None
    pdf_bytes = generate_lien_waiver_pdf(
        waiver, waiver.payment, job, get_all_settings(db)
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"LienWaiver_{waiver.waiver_type}_{waiver.payment_id}.pdf"
            )
        },
    )
