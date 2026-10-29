"""Sub-customers (#638): QuickBooks' "Parent:Sub", one level deep.

A property is a customer and each tenant a sub-customer under it. The sub
is a customer in its own right (its invoices, payments, jobs), so a tenant
is made inactive on move-out without touching the property. What ties the
two together lives here:

* ``bill_with_parent`` on the sub puts its documents on the parent's A/R:
  the parent's A/R Aging row and statement carry them, a payment from the
  parent applies to them, and the printed invoice bills the parent naming
  the sub. Off, the sub is billed and aged on its own, and only the
  parent's page rolls it up.
* ``billed_to`` answers, for any set of customers, whose A/R each one's
  documents sit on, in one query, so a report never asks per row.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.contacts import Customer
from app.models.credit_memos import CreditApplication, CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation


def _money(amount) -> str:
    return f"${Decimal(str(amount or 0)):,.2f}"


def billed_with_parent(db: Session) -> dict[int, int]:
    """Sub id → parent id, for every sub-customer billed with its parent."""
    rows = db.query(Customer.id, Customer.parent_customer_id).filter(
        Customer.parent_customer_id.isnot(None), Customer.bill_with_parent.is_(True)
    )
    return {sid: pid for sid, pid in rows}


def billed_to(db: Session, customer_ids: Optional[Iterable[int]] = None) -> dict:
    """Customer id → the id whose A/R carries its documents: the parent of
    a sub billed with it, else the customer itself. Asked for ``customer_ids``
    (every id answers, parents included) or, given none, for every sub
    billed with its parent only."""
    routed = billed_with_parent(db)
    if customer_ids is None:
        return routed
    return {cid: routed.get(cid, cid) for cid in customer_ids}


def billed_subs(db: Session, parent_id: int) -> list[Customer]:
    """The sub-customers whose documents sit on this parent's A/R."""
    return (
        db.query(Customer)
        .filter(
            Customer.parent_customer_id == parent_id,
            Customer.bill_with_parent.is_(True),
        )
        .order_by(Customer.name)
        .all()
    )


def pays_for(db: Session, payer_id: int, invoice) -> bool:
    """Whether money from ``payer_id`` may apply to ``invoice``: its own
    invoice, or the invoice of a sub-customer billed with it."""
    if invoice.customer_id == payer_id:
        return True
    sub = db.get(Customer, invoice.customer_id)
    return (
        sub is not None
        and bool(sub.bill_with_parent)
        and sub.parent_customer_id == payer_id
    )


def refuse_unless_pays_for(db: Session, payer_id: int, invoice, document: str) -> None:
    if not pays_for(db, payer_id, invoice):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invoice {invoice.invoice_number} belongs to a different "
                f"customer than this {document}."
            ),
        )


def bill_to_parent(db: Session, customer: Customer) -> Optional[Customer]:
    """The parent a sub-customer's invoices are billed to, or None when the
    customer is billed on its own."""
    if not (customer.parent_customer_id and customer.bill_with_parent):
        return None
    return db.get(Customer, customer.parent_customer_id)


def check_parent(
    db: Session, customer: Optional[Customer], parent_id: Optional[int]
) -> Optional[Customer]:
    """The parent a customer may sit under, or the sentence saying why
    not: one level deep (a sub-customer cannot have sub-customers, and a
    customer with sub-customers cannot become one), never itself."""
    if parent_id is None:
        return None
    parent = db.get(Customer, parent_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Parent customer not found")
    if customer is not None and parent.id == customer.id:
        raise HTTPException(
            status_code=422, detail="A customer cannot be its own parent."
        )
    if parent.parent_customer_id is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{parent.name} is a sub-customer of {parent.parent.name}; a "
                "sub-customer cannot have sub-customers of its own."
            ),
        )
    if customer is not None:
        subs = (
            db.query(Customer.name)
            .filter(Customer.parent_customer_id == customer.id)
            .order_by(Customer.name)
            .all()
        )
        if subs:
            names = ", ".join(n for (n,) in subs)
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{customer.name} has sub-customers of its own ({names}); "
                    "move them first."
                ),
            )
    return parent


def refuse_inactive_parent(db: Session, customer_name: str, parent_id) -> None:
    """An active sub-customer sits under an active parent (#638 review): a
    tenant made active again under a retired property would be billed to,
    and listed under, a parent no picker offers. 409 naming the parent;
    the way out is to make the parent active first, or move the sub."""
    if parent_id is None:
        return
    parent = db.get(Customer, parent_id)
    if parent is None or parent.is_active:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": "parent_inactive",
            "parent_id": parent.id,
            "message": (
                f"{customer_name} is a sub-customer of {parent.name}, which is "
                f"inactive. Make {parent.name} active first, or move "
                f"{customer_name} out from under it."
            ),
        },
    )


def parent_money_on_open_invoices(db: Session, sub: Customer) -> list[dict]:
    """The sub-customer's open invoices that a payment or credit memo of its
    parent is applied to, each with the invoice number and how much of the
    parent's money is on it (#638 review). While there are any, the sub
    stays billed with its parent: taken off it, the parent's statement
    would no longer list the invoices its money relieved."""
    if not sub.parent_customer_id:
        return []
    open_invoices = {
        inv.id: inv
        for inv in db.query(Invoice).filter(
            Invoice.customer_id == sub.id,
            Invoice.status != InvoiceStatus.VOID,
            Invoice.balance_due > 0,
        )
    }
    if not open_invoices:
        return []
    on_invoice: dict[int, Decimal] = {}
    paid = (
        db.query(PaymentAllocation.invoice_id, func.sum(PaymentAllocation.amount))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .filter(
            Payment.customer_id == sub.parent_customer_id,
            or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)),
            PaymentAllocation.invoice_id.in_(list(open_invoices)),
        )
        .group_by(PaymentAllocation.invoice_id)
    )
    credited = (
        db.query(CreditApplication.invoice_id, func.sum(CreditApplication.amount))
        .join(CreditMemo, CreditMemo.id == CreditApplication.credit_memo_id)
        .filter(
            CreditMemo.customer_id == sub.parent_customer_id,
            CreditMemo.status != CreditMemoStatus.VOID,
            CreditApplication.invoice_id.in_(list(open_invoices)),
        )
        .group_by(CreditApplication.invoice_id)
    )
    for rows in (paid, credited):
        for invoice_id, amount in rows:
            on_invoice[invoice_id] = on_invoice.get(invoice_id, Decimal(0)) + Decimal(
                str(amount or 0)
            )
    return [
        {
            "invoice_id": invoice_id,
            "invoice_number": open_invoices[invoice_id].invoice_number,
            "amount": float(amount),
        }
        for invoice_id, amount in sorted(
            on_invoice.items(), key=lambda kv: open_invoices[kv[0]].invoice_number or ""
        )
        if amount
    ]


def refuse_unbilling_with_parent(db: Session, sub: Customer) -> None:
    """Turning Bill with parent off, or moving the sub-customer to another
    parent (or none), is refused with 409 while a payment or credit of the
    parent is on the sub's open invoices (#638 review)."""
    rows = parent_money_on_open_invoices(db, sub)
    if not rows:
        return
    parent = db.get(Customer, sub.parent_customer_id)
    parent_name = parent.name if parent else "its parent"
    listed = ", ".join(f"#{r['invoice_number']} ({_money(r['amount'])})" for r in rows)
    raise HTTPException(
        status_code=409,
        detail={
            "error": "parent_money_on_open_invoices",
            "invoices": rows,
            "message": (
                f"{parent_name}'s payments or credits are on {sub.name}'s open "
                f"invoices: {listed}. {sub.name} stays billed with {parent_name} "
                f"until those invoices are settled, or the payments and credits "
                f"are voided and entered again from {sub.name}."
            ),
        },
    )


def active_subs(db: Session, parent_id: int) -> list[Customer]:
    return (
        db.query(Customer)
        .filter(Customer.parent_customer_id == parent_id, Customer.is_active.is_(True))
        .order_by(Customer.name)
        .all()
    )


def billed_subs_owing(db: Session, parent_id: int) -> list[dict]:
    """The sub-customers billed with this parent, active or not, that still
    have open invoices: each with what is open and the invoice numbers.
    Their money is owed on the parent's A/R, so the parent cannot be made
    inactive while there are any (#638 review): the open balance would age
    on an inactive customer no Receive Payment picker offers."""
    from app.services.contact_balances import home_amount

    rows = (
        db.query(Invoice, Customer)
        .join(Customer, Customer.id == Invoice.customer_id)
        .filter(
            Customer.parent_customer_id == parent_id,
            Customer.bill_with_parent.is_(True),
            Invoice.status != InvoiceStatus.VOID,
            Invoice.balance_due > 0,
        )
        .order_by(Customer.name, Invoice.invoice_number)
        .all()
    )
    out: dict[int, dict] = {}
    for inv, sub in rows:
        row = out.setdefault(
            sub.id, {"id": sub.id, "name": sub.name, "open": Decimal(0), "numbers": []}
        )
        row["open"] += home_amount(inv.balance_due, inv.exchange_rate)
        row["numbers"].append(inv.invoice_number or "")
    return list(out.values())


def refuse_inactivating_a_parent(
    db: Session, customer: Customer, with_subs: bool
) -> list[Customer]:
    """Making a parent inactive is refused (409) while a sub-customer billed
    with it still has open invoices, whatever ``with_subs`` says, and
    otherwise asks about its active sub-customers (409 naming each with its
    open balance) unless ``with_subs`` says to make them inactive too.
    Returns the subs to inactivate along with it (none when with_subs is
    off and there are none)."""
    from app.services.contact_balances import customer_balances

    owing = billed_subs_owing(db, customer.id)
    if owing:
        sentences = []
        for row in owing:
            numbers = ", ".join(f"#{n}" for n in row["numbers"] if n)
            sentences.append(
                f"{row['name']} is billed with {customer.name} and still owes "
                f"{_money(row['open'])}" + (f" ({numbers})" if numbers else "")
            )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "subs_billed_with_parent_owe",
                "message": (
                    "; ".join(sentences) + ". Receive the payment or write the "
                    "balance off first, or take Bill with parent off the "
                    f"sub-customer so it is billed on its own; then make "
                    f"{customer.name} inactive."
                ),
                "subs": [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "balance": float(row["open"]),
                        "invoices": row["numbers"],
                    }
                    for row in owing
                ],
            },
        )
    subs = active_subs(db, customer.id)
    if subs and not with_subs:
        balances = customer_balances(db, [s.id for s in subs])
        names = ", ".join(
            (
                f"{s.name} ({_money(balances[s.id])} open)"
                if balances.get(s.id)
                else s.name
            )
            for s in subs
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "active_subs",
                "message": (
                    f"{customer.name} has active sub-customers: {names}. Make "
                    "them inactive too? Pass ?with_subs=true to make the "
                    "parent and its sub-customers inactive together."
                ),
                "subs": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "balance": float(balances.get(s.id, 0)),
                    }
                    for s in subs
                ],
            },
        )
    return subs


def duplicate_candidates(db: Session, parent_id: Optional[int]) -> list[Customer]:
    """The active customers a new name is checked against for a likely
    duplicate (#638 review): the top-level ones and, for a sub-customer,
    its siblings under the same parent. "Unit 1" under a second property is
    not a duplicate of the first property's Unit 1; a bare name is a
    top-level customer, so a top-level name is checked against those."""
    q = db.query(Customer).filter(Customer.is_active.is_(True))
    if parent_id is None:
        return q.filter(Customer.parent_customer_id.is_(None)).all()
    return q.filter(
        or_(
            Customer.parent_customer_id.is_(None),
            Customer.parent_customer_id == parent_id,
        )
    ).all()
