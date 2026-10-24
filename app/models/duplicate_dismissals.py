# ============================================================================
# "Not a duplicate" on the Possible Duplicates report (#414).
#
# One row per group someone looked at and kept: the bank or card account
# and the sorted transaction ids of the group, comma-separated. A group the
# report finds later is hidden while every one of its entries is in one
# dismissed set, so voiding one of three dismissed entries keeps the other
# two hidden, and a fourth copy arriving brings the group back.
#
# The per-account key is a fixed-length sha256 of the sorted ids
# (group_hash), not the id list itself, so it never overflows a column
# however long the group is (a String(2000) key 500'd on PostgreSQL for a
# long chain). The ids stay in transaction_ids (Text) for display and the
# subset match.
# ============================================================================

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.database import Base


class DuplicateDismissal(Base):
    __tablename__ = "duplicate_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "group_hash", name="uq_duplicate_dismissals_group"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    transaction_ids = Column(Text, nullable=False)  # "12,15,31" — display/subset
    group_hash = Column(String(64), nullable=False)  # sha256 of sorted ids — the key
    dismissed_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
