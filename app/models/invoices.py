# ============================================================================
# Invoices — header + line items.
# Every invoice generates a balanced journal entry (see transactions.py).
# ============================================================================
# Fun fact: QB2003 hardcoded a max of 14 characters for invoice numbers.
# We lifted that to 50 because it's not 2003 anymore. Mostly.
# ============================================================================

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    event,
    false,
    func,
    select,
)
from sqlalchemy.orm import relationship

from app.database import Base


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"  # "Pending" in the QB2003 UI
    SENT = "sent"
    PARTIAL = "partial"
    PAID = "paid"
    VOID = "void"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.DRAFT, index=True)

    date = Column(Date, nullable=False, index=True)
    due_date = Column(Date, nullable=True)
    terms = Column(String(50), default="Net 30")
    po_number = Column(String(100), nullable=True)

    bill_address1 = Column(String(200), nullable=True)
    bill_address2 = Column(String(200), nullable=True)
    bill_city = Column(String(100), nullable=True)
    bill_state = Column(String(50), nullable=True)
    bill_zip = Column(String(20), nullable=True)
    # Whom the invoice was billed to, frozen with the street above when
    # the invoice is made (#638; 2.31.1 gate, W-59): the parent of a
    # sub-customer billed with it, else the customer itself. The printed
    # Bill To block comes from these alone (name, street, country, and
    # "For: <sub-customer>" when bill_to_customer_id is not the customer),
    # so a move or a flag change afterwards does not alter a printed
    # invoice. Filled on insert from the customer when left empty, on
    # every creation path (_freeze_bill_to below); rows from before are
    # backfilled from the customer they were billed to at the upgrade.
    bill_to_customer_id = Column(Integer, nullable=True)
    bill_to_name = Column(String(200), nullable=True)
    bill_to_country = Column(String(100), nullable=True)

    ship_address1 = Column(String(200), nullable=True)
    ship_address2 = Column(String(200), nullable=True)
    ship_city = Column(String(100), nullable=True)
    ship_state = Column(String(50), nullable=True)
    ship_zip = Column(String(20), nullable=True)

    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(7, 6), default=0)  # a fraction: 8.875% is 0.08875
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)
    amount_paid = Column(Numeric(15, 2), default=0)
    balance_due = Column(Numeric(15, 2), default=0)

    # Construction progress billing (#555): the retainage this invoice holds
    # back (to Retainage Receivable, 1150) and any held retainage it bills
    # back (released from 1150), and the owner pay application it came from.
    # All zero/null on an ordinary invoice.
    retainage_held = Column(Numeric(15, 2), nullable=False, default=0)
    retainage_released = Column(Numeric(15, 2), nullable=False, default=0)
    pay_application_id = Column(
        Integer, ForeignKey("pay_applications.id", ondelete="SET NULL"), nullable=True
    )

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # Online payments (Stripe / any registered provider)
    payment_token = Column(
        String(36),
        unique=True,
        nullable=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    stripe_checkout_session_id = Column(String(255), nullable=True)
    # Most recent checkout attempt: which provider, and its session/order id.
    # checkout_external_id is indexed because providers without metadata
    # passthrough (Square) resolve the invoice by this id on webhook/poll.
    checkout_provider = Column(String(20), nullable=True)
    checkout_external_id = Column(String(255), nullable=True, index=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    # Sales receipt = invoice + payment entered as one step (QB's "Enter
    # Sales Receipts"). Stored as a regular Invoice so reports, PDFs, and
    # exports need no special casing; the flag drives the dedicated list
    # and QB interop typing.
    is_sales_receipt = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    # Multi-currency: document currency + rate it was booked at (GL is home)
    currency = Column(String(3), nullable=True)
    exchange_rate = Column(Numeric(18, 8), nullable=True)

    # Nonprofit: a pledge prints as one (a nonprofit still invoices program
    # fees); a donation receipt states what the donor got back so the
    # deductible portion prints (IRS Pub. 1771). recurring_invoice_id links
    # a generated invoice to its template for the pledge report.
    is_pledge = Column(Boolean, nullable=False, default=False, server_default=false())
    fair_value_amount = Column(Numeric(15, 2), nullable=True)
    fair_value_description = Column(String(200), nullable=True)
    recurring_invoice_id = Column(
        Integer, ForeignKey("recurring_invoices.id"), nullable=True, index=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="invoices")
    lines = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_order",
    )
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


@event.listens_for(Invoice, "before_insert")
def _freeze_bill_to(_mapper, connection, target):
    """Freeze whom a new invoice is billed to (W-59) unless the maker set
    it (a duplicate carries its original's). One query on the customer and
    its parent, so every path that makes an invoice (the form, convert,
    duplicate, the scheduler, progress billing, the importers) freezes the
    same thing without each saying so."""
    if target.bill_to_name is not None or target.customer_id is None:
        return
    freeze_bill_to(connection, target)


def freeze_bill_to(connection, target) -> None:
    """Set the invoice's frozen Bill To from its customer as it is now: the
    parent of a sub-customer billed with it, else the customer. Called on
    insert, and by an edit that moves the invoice to another customer (the
    Bill To is the new customer's; an edit that keeps the customer keeps
    the Bill To the invoice was made with)."""
    if target.customer_id is None:
        return
    from app.models.contacts import Customer

    c = Customer.__table__
    parent = c.alias("parent")
    row = connection.execute(
        select(
            c.c.id,
            c.c.name,
            c.c.bill_country,
            c.c.bill_with_parent,
            parent.c.id,
            parent.c.name,
            parent.c.bill_country,
        )
        .select_from(c.outerjoin(parent, parent.c.id == c.c.parent_customer_id))
        .where(c.c.id == target.customer_id)
    ).first()
    if row is None:
        return
    cid, name, country, billed_with_parent, pid, pname, pcountry = row
    if billed_with_parent and pid is not None:
        target.bill_to_customer_id = pid
        target.bill_to_name = pname
        target.bill_to_country = pcountry
    else:
        target.bill_to_customer_id = cid
        target.bill_to_name = name
        target.bill_to_country = country


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 4), default=1)
    # Unit price to four places: bulk goods are priced like $0.045 a box.
    # The line amount is still rounded to the cent (accounting._q).
    rate = Column(Numeric(17, 4), default=0)
    amount = Column(Numeric(15, 2), default=0)
    class_name = Column(String(100), nullable=True)
    # Per-line job / class; NULL falls back to the transaction header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Per-line cost type and unit cost, carried over from the estimate a
    # converted invoice came from (#551) so the cost basis isn't lost; NULL
    # on an ordinary invoice line.
    cost_type = Column(String(20), nullable=True)
    unit_cost = Column(Numeric(12, 4), nullable=True)
    # Per-line sales tax (default: the item's flag, or taxable). A customer-
    # owned-device repair is labor with no tax; the part on the same invoice
    # is taxed.
    is_taxable = Column(Boolean, nullable=False, default=True)
    line_order = Column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="lines")
    item = relationship("Item")
