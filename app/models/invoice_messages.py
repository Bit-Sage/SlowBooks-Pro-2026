# ============================================================================
# Invoice Messages — the saved notes an invoice can carry (#392)
# ============================================================================

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy import false as sa_false

from app.database import Base


class InvoiceMessage(Base):
    """A saved message chosen on the invoice form and printed in its Notes.

    The invoice keeps the text itself (no foreign key), so editing a
    message later leaves the invoices already written alone. At most one
    message is the default; it fills Notes on a new invoice.
    """

    __tablename__ = "invoice_messages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    body = Column(Text, nullable=False)
    is_default = Column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
