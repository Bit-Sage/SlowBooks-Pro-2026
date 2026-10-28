from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel

WaiverType = Literal[
    "conditional_progress",
    "unconditional_progress",
    "conditional_final",
    "unconditional_final",
]


class LienWaiverCreate(StrictModel):
    """A waiver for one payment. The job, application and amount come from the
    payment; the through date defaults to the application's period (else the
    payment's date)."""

    payment_id: int
    waiver_type: WaiverType
    through_date: Optional[dt_date] = None
    signed_by: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class LienWaiverOut(BaseModel):
    id: int
    payment_id: int
    job_id: Optional[int] = None
    pay_application_id: Optional[int] = None
    waiver_type: str
    amount: Decimal
    through_date: Optional[dt_date] = None
    signed_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
