# ============================================================================
# Trade packages (#550) — the bid packages a general contractor awards.
#
# A package (Concrete, Drywall and Ceilings, HVAC...) is what a sub bids and
# gets awarded; a cost code SECTION is what a cost posts to. The two are
# many-to-many: a package can span divisions, and a division can be split
# across packages. Nothing references a package yet (later steps add bids and
# commitments), so a package deletes freely; the is_active flag is kept for
# those steps.
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


class TradePackage(Base):
    __tablename__ = "trade_packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sections = relationship(
        "TradePackageSection",
        back_populates="package",
        cascade="all, delete-orphan",
    )


class TradePackageSection(Base):
    __tablename__ = "trade_package_sections"

    # Composite primary key: a section maps to a package at most once.
    package_id = Column(
        Integer,
        ForeignKey("trade_packages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cost_code_id = Column(
        Integer,
        ForeignKey("cost_codes.id", ondelete="CASCADE"),
        primary_key=True,
    )

    package = relationship("TradePackage", back_populates="sections")
    cost_code = relationship("CostCode")
