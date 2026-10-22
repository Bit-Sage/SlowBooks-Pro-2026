from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, StringConstraints

from app.schemas.common import StrictModel

# The name is what the invoice form's list shows; a blank one has nothing
# to pick. The body is what the invoice prints; a blank one prints nothing.
MessageName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
MessageBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InvoiceMessageCreate(StrictModel):
    name: MessageName
    body: MessageBody
    is_default: bool = False


class InvoiceMessageUpdate(StrictModel):
    name: Optional[MessageName] = None
    body: Optional[MessageBody] = None
    is_default: Optional[bool] = None


class InvoiceMessageResponse(BaseModel):
    id: int
    name: str
    body: str
    is_default: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
