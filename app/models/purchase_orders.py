# ============================================================================
# Purchase Orders — non-posting documents to vendors, convertible to bills
# Feature 6: Structurally similar to Estimates but vendor-facing
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class POStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIAL = "partial"
    RECEIVED = "received"
    CLOSED = "closed"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(50), unique=True, nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    status = Column(Enum(POStatus), default=POStatus.DRAFT)

    date = Column(Date, nullable=False)
    expected_date = Column(Date, nullable=True)
    ship_to = Column(Text, nullable=True)

    # Construction (#553): a commitment is a purchase order. In construction
    # mode a PO is either a plain "po" or a "subcontract"; its lines are its
    # schedule of values, and it bills in parts. The package it is bought
    # against, the retainage held on every bill from it, and the scope.
    commitment_type = Column(String(12), nullable=False, default="po")
    trade_package_id = Column(
        Integer, ForeignKey("trade_packages.id", ondelete="SET NULL"), nullable=True
    )
    retainage_pct = Column(Numeric(5, 2), nullable=True)
    scope_notes = Column(Text, nullable=True)

    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(7, 6), default=0)  # a fraction: 8.875% is 0.08875
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)

    notes = Column(Text, nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vendor = relationship("Vendor", backref="purchase_orders")
    job = relationship("Job")
    trade_package = relationship("TradePackage")
    lines = relationship(
        "PurchaseOrderLine",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderLine.line_order",
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(
        Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 4), default=1)
    rate = Column(Numeric(17, 4), default=0)  # unit price, to 4 places
    amount = Column(Numeric(15, 2), default=0)  # the line's scheduled value
    # Construction (#553): how much of this line has been billed so far.
    # remaining = amount - billed_amount. received_qty is left for the
    # goods-receipt path; billing tracks dollars.
    billed_amount = Column(Numeric(15, 2), nullable=False, default=0)
    received_qty = Column(Numeric(12, 4), default=0)
    # Per-line job; NULL falls back to the document header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Per-line cost type; NULL falls back to the cost code's default.
    cost_type = Column(String(20), nullable=True)
    line_order = Column(Integer, default=0)

    purchase_order = relationship("PurchaseOrder", back_populates="lines")
    item = relationship("Item")
