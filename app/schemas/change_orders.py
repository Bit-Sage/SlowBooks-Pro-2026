from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.models.change_orders import CO_FUNDING, CO_KINDS
from app.schemas.common import StrictModel


class ChangeOrderLineIn(StrictModel):
    description: Optional[str] = None
    cost_type: str
    cost_code_id: Optional[int] = None
    trade_package_id: Optional[int] = None
    quantity: Decimal = Decimal("1")
    unit: Optional[str] = None
    unit_cost: Decimal = Decimal("0")
    markup_pct: Optional[Decimal] = None


class ChangeOrderCreate(StrictModel):
    job_id: int
    kind: str = "owner"
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: str = "contract"
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    lines: List[ChangeOrderLineIn] = []

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "owner").strip().lower()
        if v not in CO_KINDS:
            raise ValueError(f"kind must be one of {', '.join(CO_KINDS)}")
        return v

    @field_validator("funding")
    @classmethod
    def _funding(cls, v):
        v = (v or "contract").strip().lower()
        if v not in CO_FUNDING:
            raise ValueError(f"funding must be one of {', '.join(CO_FUNDING)}")
        return v


class ChangeOrderUpdate(StrictModel):
    """Edit a draft/pending change order. A field left out is unchanged."""

    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: Optional[str] = None
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    lines: Optional[List[ChangeOrderLineIn]] = None

    @field_validator("funding")
    @classmethod
    def _funding(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if v not in CO_FUNDING:
            raise ValueError(f"funding must be one of {', '.join(CO_FUNDING)}")
        return v


class PromoteIn(StrictModel):
    pass


class SubOrderIn(StrictModel):
    commitment_id: int
    line_ids: Optional[List[int]] = None  # None = the subcontract lines


class ChangeOrderLineOut(BaseModel):
    id: int
    line_order: Optional[int] = None
    description: Optional[str] = None
    cost_type: str
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    trade_package_id: Optional[int] = None
    trade_package_name: Optional[str] = None
    quantity: Decimal
    unit: Optional[str] = None
    unit_cost: Decimal
    amount: Decimal
    markup_pct: Optional[Decimal] = None
    sell_amount: Decimal

    model_config = {"from_attributes": True}


class ChangeOrderOut(BaseModel):
    id: int
    job_id: int
    number: str
    kind: str
    status: str
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: str
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    source_pco_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    voided_at: Optional[datetime] = None
    lines: List[ChangeOrderLineOut] = []
    # Server-computed roll-ups (cost / markup / sell, and per cost type).
    cost_total: float = 0.0
    markup_total: float = 0.0
    sell_total: float = 0.0
    by_cost_type: dict = {}

    model_config = {"from_attributes": True}
