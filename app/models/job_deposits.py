# ============================================================================
# Owner deposits (#555) — money a customer pays before (or ahead of) the
# work, held on Customer Deposits (2210) until a pay application applies it.
#
# A deposit posts DR bank / CR 2210 through create_journal_entry
# (source_type "job_deposit"); a pay application's deposit_applied draws it
# down. The contract summary reads received / applied / unapplied.
# ============================================================================

from sqlalchemy import (
    Column,
    Integer,
    Date,
    Numeric,
    DateTime,
    Text,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class JobDeposit(Base):
    __tablename__ = "job_deposits"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = Column(Date, nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    deposit_to_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    memo = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job")
    deposit_to_account = relationship("Account", foreign_keys=[deposit_to_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
