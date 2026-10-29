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

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.contacts import Customer


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


def active_subs(db: Session, parent_id: int) -> list[Customer]:
    return (
        db.query(Customer)
        .filter(Customer.parent_customer_id == parent_id, Customer.is_active.is_(True))
        .order_by(Customer.name)
        .all()
    )


def refuse_inactivating_a_parent(
    db: Session, customer: Customer, with_subs: bool
) -> list[Customer]:
    """Making a parent inactive asks about its active sub-customers (409
    naming them) unless ``with_subs`` says to make them inactive too.
    Returns the subs to inactivate along with it (none when with_subs is
    off and there are none)."""
    subs = active_subs(db, customer.id)
    if subs and not with_subs:
        names = ", ".join(s.name for s in subs)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "active_subs",
                "message": (
                    f"{customer.name} has active sub-customers: {names}. Make "
                    "them inactive too? Pass ?with_subs=true to make the "
                    "parent and its sub-customers inactive together."
                ),
                "subs": [{"id": s.id, "name": s.name} for s in subs],
            },
        )
    return subs
