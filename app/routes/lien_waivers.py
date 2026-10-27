# ============================================================================
# Lien waivers (#555) — conditional / unconditional, progress / final, one per
# payment and type. The record holds the payment's amount and the through
# date; GET /{id}/pdf prints it.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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
    pay_application_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(LienWaiver)
    if payment_id is not None:
        q = q.filter(LienWaiver.payment_id == payment_id)
    if pay_application_id is not None:
        q = q.filter(LienWaiver.pay_application_id == pay_application_id)
    return q.order_by(LienWaiver.id).all()


@router.post("", response_model=LienWaiverOut, status_code=201)
def create_lien_waiver(data: LienWaiverCreate, db: Session = Depends(get_db)):
    payment = db.get(Payment, data.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(
            status_code=409, detail="A voided payment can't have a lien waiver."
        )
    # The job and payment application come from the invoice the payment pays.
    invoice = (
        db.query(Invoice)
        .join(PaymentAllocation, PaymentAllocation.invoice_id == Invoice.id)
        .filter(PaymentAllocation.payment_id == payment.id)
        .order_by(Invoice.id)
        .first()
    )
    job_id = invoice.job_id if invoice else None
    app = db.get(PayApplication, invoice.pay_application_id) if (
        invoice and invoice.pay_application_id
    ) else None
    waiver = LienWaiver(
        payment_id=payment.id,
        job_id=job_id,
        pay_application_id=app.id if app else None,
        waiver_type=data.waiver_type,
        amount=Decimal(str(payment.amount)),
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
            detail="This payment already has that kind of lien waiver.",
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
