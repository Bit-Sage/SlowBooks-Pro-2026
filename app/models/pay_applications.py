# ============================================================================
# Pay applications (#555) — the owner's progress billing from the schedule of
# values. An application bills a period against the SOV: previous, this
# period, stored materials, retainage held and released, with a balance to
# finish. Approving one creates the invoice and (if a deposit applies) the
# payment that applies it; voiding reverses both.
#
# Nothing here posts; the math lives in app/services/pay_applications.py and
# the effects in app/routes/pay_applications.py. A line reads its `previous`
# from history (earlier approved applications) live, so nothing stored goes
# stale when an application is voided.
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

PAY_APP_STATUSES = ("draft", "submitted", "approved", "void")


class PayApplication(Base):
    __tablename__ = "pay_applications"
    __table_args__ = (
        UniqueConstraint("job_id", "application_no", name="uq_pay_app_no"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1, 2, … per job; the printed number is PA-<job>-<no:02>.
    application_no = Column(Integer, nullable=False)
    number = Column(String(20), unique=True, nullable=False)
    period_to = Column(Date, nullable=True)
    date = Column(Date, nullable=True)
    status = Column(String(10), nullable=False, default="draft")
    # Copied from the job at creation, editable while draft.
    retainage_pct = Column(Numeric(5, 2), nullable=True)
    # Retainage previously held that this application bills back (<= held).
    release_retainage = Column(Numeric(15, 2), nullable=False, default=0)
    # Owner deposit applied to this application's current due.
    deposit_applied = Column(Numeric(15, 2), nullable=False, default=0)
    # The waiver sent with this application (not_required/pending/…).
    lien_waiver_status = Column(String(16), nullable=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    approved_at = Column(DateTime(timezone=True), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    invoice = relationship("Invoice", foreign_keys=[invoice_id])
    lines = relationship(
        "PayApplicationLine",
        back_populates="pay_application",
        cascade="all, delete-orphan",
        order_by="PayApplicationLine.line_no",
    )


class PayApplicationLine(Base):
    __tablename__ = "pay_application_lines"

    id = Column(Integer, primary_key=True, index=True)
    pay_application_id = Column(
        Integer,
        ForeignKey("pay_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sov_line_id = Column(
        Integer, ForeignKey("sov_lines.id", ondelete="SET NULL"), nullable=True
    )
    line_no = Column(Integer, nullable=False)
    item_no = Column(String(10), nullable=True)
    description = Column(String(200), nullable=True)
    scheduled_value = Column(Numeric(15, 2), nullable=False, default=0)
    # this period's work, and materials presently stored (not cumulative).
    this_period = Column(Numeric(15, 2), nullable=False, default=0)
    stored = Column(Numeric(15, 2), nullable=False, default=0)

    pay_application = relationship("PayApplication", back_populates="lines")
    sov_line = relationship("SovLine")
