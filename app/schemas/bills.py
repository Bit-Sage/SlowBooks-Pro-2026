from datetime import date as dt_date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator, field_serializer

from app.schemas.common import (
    StrictModel,
    TaxRateFloat,
    TaxRateOut,
    validate_non_negative_line,
    quantity_text,
)
from app.schemas.invoices import RateOut


class BillLineCreate(StrictModel):
    """One line of a bill: a quantity and a rate, or an amount alone (#391).

    A utility bill or a pest-control bill has an amount and no quantity or
    rate, so a line may send ``amount`` by itself. The row is stored as
    quantity 1 x rate = amount, which is what the bill says and what every
    reader of the line (the journal, job costing, the PDF, the exports)
    already works from; nothing downstream learns a new shape. A line that
    sends a quantity or a rate is priced from them as before, and an amount
    sent with them must agree to the cent or the line is refused: a client
    that sends both and means the amount would otherwise be told nothing.
    """

    item_id: Optional[int] = None
    account_id: Optional[int] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    description: Optional[str] = None
    quantity: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    line_order: int = 0

    @model_validator(mode="after")
    def _price_the_line(self):
        sent = None if self.amount is None else _cents(Decimal(str(self.amount)))
        if sent is not None and sent < 0:
            raise ValueError(
                "amount must be non-negative; use a vendor credit for refunds"
            )
        if sent and not self.quantity and not self.rate:
            # No quantity and no rate (absent, null or 0): the amount is the line.
            self.quantity, self.rate = 1, float(sent)
        else:
            # Priced from the quantity and rate; with neither and no amount
            # it is a description-only line, as before.
            self.quantity = 1 if self.quantity is None else self.quantity
            self.rate = self.rate or 0
        validate_non_negative_line(self.quantity, self.rate)
        priced = _cents(Decimal(str(self.quantity)) * Decimal(str(self.rate)))
        if sent is not None and abs(sent - priced) > Decimal("0.01"):
            raise ValueError(
                f"amount {sent:.2f} is not quantity x rate ({priced:.2f}); "
                "send the quantity and rate, or the amount alone"
            )
        self.amount = float(priced)
        return self


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class BillLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class BillCreate(StrictModel):
    vendor_id: int
    # The vendor's invoice number; blank → generated (date + vendor initials).
    bill_number: Optional[str] = None
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: str = "Net 30"
    ref_number: Optional[str] = None
    po_id: Optional[int] = None
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: list[BillLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("bill must have at least one line")
        return v


class BillUpdate(StrictModel):
    """An edit of a posted bill (#225): whatever is sent changes, the rest
    stays. Lines, when sent, replace the bill's lines."""

    vendor_id: Optional[int] = None
    bill_number: Optional[str] = None
    date: Optional[dt_date] = None
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: Optional[list[BillLineCreate]] = None


class BillResponse(BaseModel):
    id: int
    bill_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    status: str
    po_id: Optional[int] = None
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    subtotal: Decimal = Decimal("0")
    tax_rate: TaxRateOut = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: list[BillLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class BillPaymentAllocationCreate(StrictModel):
    bill_id: int
    amount: float


class BillPaymentCreate(StrictModel):
    vendor_id: int
    date: dt_date
    amount: float
    method: Optional[str] = None
    check_number: Optional[str] = None
    pay_from_account_id: Optional[int] = None
    notes: Optional[str] = None
    # No class_id or job_id (v2.22.0, #234): the schema accepted both and the
    # route dropped them — BillPayment has no column and the payment's
    # journal never carried them — so a client that sent one was told
    # nothing. Nothing sends them (Pay Bills offers neither), and a body
    # that does is now refused like any other unknown field.
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    allocations: list[BillPaymentAllocationCreate] = []


class BillPaymentAllocationResponse(BaseModel):
    bill_id: int
    amount: Decimal = Decimal("0")
    model_config = {"from_attributes": True}


class BillPaymentResponse(BaseModel):
    id: int
    vendor_id: int
    vendor_name: Optional[str] = None
    date: dt_date
    amount: Decimal = Decimal("0")
    method: Optional[str] = None
    check_number: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    is_voided: bool = False
    created_at: Optional[datetime] = None
    # Which bills this payment paid, and how much of each — the bill's view
    # lists its payments from here so one can be voided on screen.
    allocations: list[BillPaymentAllocationResponse] = []
    model_config = {"from_attributes": True}
