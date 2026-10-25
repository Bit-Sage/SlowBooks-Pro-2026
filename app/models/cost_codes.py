# ============================================================================
# Cost codes — the job-costing chart. A cost code is what a contractor
# budgets and tracks a job by ("03 Concrete", "03 30 00 Cast-in-Place
# Concrete", "L-100 Rough carpentry labor"), independent of the G/L account
# the cost posts to: the account says *what kind of expense*, the cost code
# says *which part of the job*.
#
# Codes follow the two-level division/section numbering used in commercial
# construction specifications — a `level` of "division" ("DD") or "section"
# ("DD SS SS", optional ".SS") — and a section sits under its division through
# parent_id. A code that isn't division/section shaped (L-100, EQ) keeps level
# null and is left as typed. Each code carries a cost type as its per-line
# default (labor / material / subcontract / equipment / other); the effective
# type is read from the line first, so a line may override it.
#
# Codes referenced by any posted line are archived, never deleted. The
# standard division titles and a modest set of common sections are loaded on
# request (app/services/cost_code_catalog.py), not forced on companies that
# use their own numbering.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

# Cost types live in the cost_types table (app/models/job_costing.py); a
# code's cost_type is that table's `code` key.


class CostCode(Base):
    __tablename__ = "cost_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    cost_type = Column(String(20), nullable=False, default="other")
    # Level: "division", "section", or NULL for a custom (non-standard) code.
    # Derived from the code's shape on save and import.
    level = Column(String(10), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    # Hierarchy: division > section > sub-code, any depth
    parent_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account = relationship("Account")
    parent = relationship("CostCode", remote_side="CostCode.id", backref="children")

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}".strip()
