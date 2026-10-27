# ============================================================================
# Lien waivers (#555) — the waiver the contractor gives its owner with each
# payment. One record per payment and type: conditional or unconditional,
# progress or final. No accounting; the record prints a waiver (PDF) with the
# payment's amount and the through date.
# ============================================================================

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

# conditional = effective once the payment is received; unconditional = the
# payment has been received. progress = work through a date; final = the job.
LIEN_WAIVER_TYPES = (
    "conditional_progress",
    "unconditional_progress",
    "conditional_final",
    "unconditional_final",
)


class LienWaiver(Base):
    __tablename__ = "lien_waivers"
    __table_args__ = (
        UniqueConstraint("payment_id", "waiver_type", name="uq_lien_waiver_payment_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    pay_application_id = Column(
        Integer, ForeignKey("pay_applications.id", ondelete="SET NULL"), nullable=True
    )
    waiver_type = Column(String(24), nullable=False)
    # The payment's amount at the time the waiver was made.
    amount = Column(Numeric(15, 2), nullable=False)
    # Work is waived through this date (a final waiver covers the whole job).
    through_date = Column(Date, nullable=True)
    signed_by = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    payment = relationship("Payment")
    job = relationship("Job")
    pay_application = relationship("PayApplication")
