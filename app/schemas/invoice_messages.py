from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, StringConstraints

from app.schemas.common import StrictModel

# The name is what the invoice form's list shows; a blank one has nothing
# to pick. The body is what the invoice prints; a blank one prints nothing.
MessageName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
MessageBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# 'invoice' messages fill an invoice's Notes (#392); 'giving_statement'
# messages open the year-end giving statement (#415).
MessageKind = Literal["invoice", "giving_statement"]

# A giving-statement opening message prints above the gift table; a pasted
# letter would push the table off the page, so cap it. Invoice messages are
# a line or two and stay uncapped, as #392 shipped them.
GIVING_STATEMENT_BODY_MAX = 1500


class InvoiceMessageCreate(StrictModel):
    name: MessageName
    body: MessageBody
    kind: MessageKind = "invoice"
    is_default: bool = False


class InvoiceMessageUpdate(StrictModel):
    name: Optional[MessageName] = None
    body: Optional[MessageBody] = None
    kind: Optional[MessageKind] = None
    is_default: Optional[bool] = None


class InvoiceMessageResponse(BaseModel):
    id: int
    name: str
    body: str
    kind: str
    is_default: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
