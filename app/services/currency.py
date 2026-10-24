# ============================================================================
# Multi-currency helpers.
#
# Design: the GENERAL LEDGER is single-currency (the home currency from
# settings). Foreign-currency documents store their own currency + the
# exchange rate they were booked at; every journal line they post is
# converted to home currency at that rate. This keeps every existing GL
# invariant (books balance, void symmetry, report math) untouched — the
# dimension lives on documents, not on ledger lines.
#
# Realized FX gain/loss appears when cash settles at a different rate
# than the document was booked at (customer payments). It posts to a
# dedicated expense account (get-or-create, number 6999) — a debit there
# is a loss, a credit a gain.
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType, DetailType
from app.services.accounting import _q
from app.services.settings_service import get_all_settings

FX_ACCOUNT_NUMBER = "6999"
FX_ACCOUNT_NAME = "Exchange Gain/Loss"


def home_currency(db: Session) -> str:
    return (get_all_settings(db).get("home_currency") or "USD").upper()


def resolve_rate(
    db: Session, currency: Optional[str], provided_rate: Optional[Decimal]
) -> tuple[str, Decimal]:
    """Normalize a document's (currency, exchange_rate) pair.

    Home-currency documents always get rate 1. Foreign documents use the
    operator-provided rate when given; otherwise the Bank of Canada feed
    is consulted, and if no rate is available the request fails with a
    400 asking for an explicit rate — silently booking at 1.0 would
    corrupt the ledger.
    """
    home = home_currency(db)
    code = (currency or home).upper()
    if code == home:
        return code, Decimal("1")
    if provided_rate is not None:
        rate = Decimal(str(provided_rate))
        if rate <= 0:
            raise HTTPException(status_code=400, detail="Exchange rate must be > 0")
        return code, rate
    from app.services.fx_service import get_rate

    result = get_rate(code, home)
    if result.get("rate"):
        return code, result["rate"]
    raise HTTPException(
        status_code=400,
        detail=(
            f"No exchange rate available for {code}->{home}; "
            f"supply exchange_rate explicitly"
        ),
    )


def to_home(amount, rate: Decimal) -> Decimal:
    return _q(Decimal(str(amount)) * rate)


def convert_lines(lines: list[dict], rate: Decimal) -> list[dict]:
    """Convert journal lines to home currency at `rate`, preserving balance.

    Each line is quantized independently, so rounding can drift a cent or
    two between total debits and credits; the residual lands on the
    largest line to keep the entry balanced (the same
    quantize-at-the-boundary convention as everywhere else).
    """
    if rate == 1:
        return lines
    converted = []
    for line in lines:
        converted.append(
            {
                **line,
                "debit": to_home(line.get("debit", 0), rate),
                "credit": to_home(line.get("credit", 0), rate),
            }
        )
    drift = sum(ln["debit"] for ln in converted) - sum(ln["credit"] for ln in converted)
    if drift != 0:
        target = max(converted, key=lambda ln: max(ln["debit"], ln["credit"]))
        if target["debit"] > 0:
            target["debit"] -= drift
        else:
            target["credit"] += drift
    return converted


def fx_gain_loss_account_id(db: Session) -> int:
    acct = db.query(Account).filter(Account.account_number == FX_ACCOUNT_NUMBER).first()
    if not acct:
        acct = db.query(Account).filter(Account.name == FX_ACCOUNT_NAME).first()
    if not acct:
        acct = Account(
            name=FX_ACCOUNT_NAME,
            account_number=FX_ACCOUNT_NUMBER,
            account_type=AccountType.EXPENSE,
            # below the operating line, as QuickBooks files it (#390)
            detail_type=DetailType.OTHER_EXPENSE,
            is_system=True,
        )
        db.add(acct)
        db.flush()
    return acct.id


def document_currency(doc, db: Session) -> str:
    return (getattr(doc, "currency", None) or home_currency(db)).upper()
