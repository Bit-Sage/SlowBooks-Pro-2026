# ============================================================================
# Provider-payment recorder — the single accounting seam for every online
# payment provider (webhook AND polling paths both land here).
#
# Extracted verbatim from the Stripe webhook handler so PayPal/Square get
# the exact same row-lock idempotency, balance capping, and journal shape
# instead of re-implementing (and drifting from) it.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_undeposited_funds_id,
)
from app.services.payments.clearing import (
    clearing_account_id,
    clearing_enabled,
    fee_account_id,
)
from app.services.donor_documents import document_label
from app.services.settings_service import get_all_settings
from app.services.terminology import terms_from_db


def record_provider_payment(
    db: Session,
    provider_name: str,
    provider_display_name: str,
    invoice_id: int,
    external_id: str,
    amount: Decimal,
    fee: Optional[Decimal] = None,
) -> str:
    """Record a captured online payment against an invoice.

    Returns a status string: "payment_recorded" | "already_processed" |
    "invoice_not_found" | "invoice_already_settled".

    Idempotency under contention: providers retry webhooks with backoff on
    any non-2xx; two deliveries can land milliseconds apart (and a poll can
    race a webhook). A plain check-then-insert against Payment.reference
    would let both pass the existence guard and create two payments.
    Row-lock the invoice so the second arrival serializes behind the first
    and sees the already-recorded payment on its idempotency re-check.
    """
    # Defensive: Payment.reference is String(100); every current provider's
    # ids fit, but a silent truncation would break idempotency.
    if len(external_id) > 100:
        raise ValueError(f"external_id too long for Payment.reference: {external_id}")

    invoice = (
        db.query(Invoice).filter(Invoice.id == invoice_id).with_for_update().first()
    )
    if not invoice:
        return "invoice_not_found"

    existing = db.query(Payment).filter(Payment.reference == external_id).first()
    if existing:
        return "already_processed"

    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        return "invoice_already_settled"

    # Cap at balance due
    if amount > invoice.balance_due:
        amount = invoice.balance_due

    payment = Payment(
        customer_id=invoice.customer_id,
        date=date.today(),
        amount=amount,
        method=provider_name,
        reference=external_id,
        notes=f"Online payment via {provider_display_name}",
    )
    db.add(payment)
    db.flush()

    db.add(
        PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount)
    )

    invoice.amount_paid += amount
    invoice.balance_due -= amount
    if invoice.balance_due <= 0:
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIAL

    customer_name = invoice.customer.name if invoice.customer else "Customer"
    ar_id = get_ar_account_id(db)

    if clearing_enabled(get_all_settings(db), provider_name):
        # Clearing on: gross posts to the provider's clearing account (not
        # Undeposited Funds), and the reported fee — if any — posts to the
        # fee account in the same entry. The clearing balance is gross − fee;
        # the net payout lands against it at #584.
        clearing_id = clearing_account_id(db, provider_name)
        journal_lines = [
            {
                "account_id": clearing_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"{provider_display_name} payment from {customer_name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"{provider_display_name} payment from {customer_name}",
            },
        ]
        if fee is not None and fee > 0:
            fee_id = fee_account_id(db, provider_name)
            journal_lines += [
                {
                    "account_id": fee_id,
                    "debit": fee,
                    "credit": Decimal("0"),
                    "description": f"{provider_display_name} fee",
                },
                {
                    "account_id": clearing_id,
                    "debit": Decimal("0"),
                    "credit": fee,
                    "description": f"{provider_display_name} fee",
                },
            ]
        txn = create_journal_entry(
            db,
            date.today(),
            f"{provider_display_name} payment — "
            f"{document_label(invoice, terms_from_db(db))} #{invoice.invoice_number}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
            reference=external_id,
        )
        payment.transaction_id = txn.id
        db.commit()
        return "payment_recorded"

    # Clearing off: DR Undeposited Funds, CR A/R (unchanged).
    deposit_id = get_undeposited_funds_id(db)

    if ar_id and deposit_id:
        journal_lines = [
            {
                "account_id": deposit_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"{provider_display_name} payment from {customer_name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"{provider_display_name} payment from {customer_name}",
            },
        ]
        txn = create_journal_entry(
            db,
            date.today(),
            f"{provider_display_name} payment — "
            f"{document_label(invoice, terms_from_db(db))} #{invoice.invoice_number}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
            reference=external_id,
        )
        payment.transaction_id = txn.id

    db.commit()
    return "payment_recorded"
