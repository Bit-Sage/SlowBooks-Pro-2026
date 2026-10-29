# ============================================================================
# Payroll — pay runs, withholding, pay stubs, direct deposit
# Posts: DR Wages Expense + DR Payroll Tax Expense, CR tax/deduction payables,
#        CR the net pay account: a bank, or a liability when the wages are
#        paid later (#620; the accounts are settings, services/payroll_accounts.py).
# ============================================================================

from fastapi import APIRouter

router = APIRouter(prefix="/api/payroll", tags=["payroll"])
