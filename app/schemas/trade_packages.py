from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.schemas.common import StrictModel


def _clean_name(v):
    v = (v or "").strip()
    if not v:
        raise ValueError("Trade package name is required")
    if len(v) > 100:
        raise ValueError("Trade package name must be 100 characters or fewer")
    return v


class TradePackageSectionOut(BaseModel):
    id: int
    code: str
    label: str


class TradePackageCreate(StrictModel):
    name: str
    notes: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0
    # Section cost-code ids this package bids; replaces the mapping when given.
    section_ids: Optional[list[int]] = None

    _name = field_validator("name")(_clean_name)


class TradePackageUpdate(StrictModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    section_ids: Optional[list[int]] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return None if v is None else _clean_name(v)


class TradePackageResponse(BaseModel):
    id: int
    name: str
    notes: Optional[str] = None
    is_active: bool
    sort_order: int
    section_ids: list[int] = []
    sections: list[TradePackageSectionOut] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
