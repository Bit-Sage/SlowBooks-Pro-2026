# ============================================================================
# Email Log — tracks all emails sent from the system
# Feature 8: Invoice email delivery via SMTP
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, Text, func

from app.database import Base


class EmailLog(Base):
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False)  # invoice, estimate, statement
    entity_id = Column(Integer, nullable=False)
    recipient = Column(String(200), nullable=False)
    # Copies on the send (#611): the Cc addresses as sent, and the Bcc
    # addresses so the log says who was blind-copied even though the mail
    # itself never does. Comma-separated, null when none.
    cc = Column(String(500), nullable=True)
    bcc = Column(String(500), nullable=True)
    subject = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False)  # sent, failed
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
