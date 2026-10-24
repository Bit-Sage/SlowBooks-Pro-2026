from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class ReportRequest(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ReportLineItem(BaseModel):
    account_name: str
    account_number: Optional[str] = None
    amount: Decimal


class ProfitLossReport(BaseModel):
    start_date: date
    end_date: date
    income: list[ReportLineItem]
    cogs: list[ReportLineItem]
    expenses: list[ReportLineItem]
    total_income: Decimal
    total_cogs: Decimal
    gross_profit: Decimal
    operating_gross_profit: Decimal | None = None
    total_expenses: Decimal
    net_income: Decimal


class BalanceSheetReport(BaseModel):
    as_of_date: date
    assets: list[ReportLineItem]
    liabilities: list[ReportLineItem]
    equity: list[ReportLineItem]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal


class AgingItem(BaseModel):
    customer_name: str
    customer_id: int
    current: Decimal
    over_30: Decimal
    over_60: Decimal
    over_90: Decimal
    total: Decimal


class ARAgingReport(BaseModel):
    as_of_date: date
    items: list[AgingItem]
    totals: AgingItem
