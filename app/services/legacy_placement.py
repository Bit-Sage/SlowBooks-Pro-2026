"""Where 2.27.0's cash flow statement placed a balance-sheet account by its
number and name (#390), kept in one place.

Before the account detail types, `cash_flow._place` guessed current from
non-current: a four-digit asset number 1000-1499 was working capital and
1500-1999 investing; otherwise an asset named like equipment was
investing; a liability named like long-term debt (loan, note payable,
mortgage, line of credit, ...) was financing unless it was a credit card;
accumulated depreciation, by name, was an adjustment. Two things read the
rule now:

- the detail types migration (4ac01efbd72a) backfills every existing
  account from it, so an upgraded company's statements move no figure;
- `default_detail_type(..., name=...)` gives a NEW account created with no
  detail type the one the rule implies (the owner's call for 2.28.0): an
  asset the rule calls non-current is a Fixed Asset, a liability it calls
  long-term debt a Long Term Liability.

The rules are 2.26.0 / 2.27.0's and do not change: the migration's backfill
must give the same answer for a company file upgraded next year as for one
upgraded today. A different rule for new accounts belongs beside these, not
in them.

Nothing here imports the app, so the migration can read it.
"""

from __future__ import annotations

import re

# cash_flow.py, 2.26.0 / 2.27.0
ACCUMULATED = re.compile(r"\baccum(ulated|\.)?\s*(depr|amort)", re.I)
FIXED_ASSET = re.compile(
    r"\b(equipment|vehicles?|furniture|fixtures?|buildings?|land|machinery|"
    r"leasehold|property|computers?|fixed assets?|intangible|goodwill)\b",
    re.I,
)
LONG_TERM_DEBT = re.compile(
    r"\b(loans?|notes? payable|mortgages?|long[- ]term|line of credit|"
    r"lease liabilit(y|ies)|bonds? payable)\b",
    re.I,
)


def _type(account_type) -> str:
    """'asset', 'liability', ... from an AccountType, its value or its stored
    NAME ('ASSET', as the migration reads it)."""
    return str(getattr(account_type, "value", account_type) or "").lower()


def asset_side_by_number(number) -> str | None:
    """'current' for a four-digit asset number 1000-1499, 'non_current' for
    1500-1999, None when the number does not decide (none, or not 1xxx)."""
    number = str(number or "").strip()
    if number.isdigit() and len(number) == 4 and number.startswith("1"):
        return "current" if number < "1500" else "non_current"
    return None


def placement(account_type, name, number=None, bank_kind=None) -> str | None:
    """Where the 2.27.0 rule put a balance-sheet account, before the
    fixed-asset register (which only the migration and the cash flow know):

    - 'accumulated': accumulated depreciation by name (an adjustment);
    - 'fixed': an asset numbered 1500-1999 and named like equipment, or
      unnumbered and named like equipment (investing);
    - 'other': an asset numbered 1500-1999 not named like equipment
      (investing);
    - 'current': an asset numbered 1000-1499, whatever its name;
    - 'long_term': a liability, not a credit card, named like long-term
      debt (financing);
    - None: the rule says nothing, the account is working capital by
      default (and any income, expense or equity account).

    A bank account is current and a credit card current, by their kind."""
    atype = _type(account_type)
    name = name or ""
    if atype == "asset":
        if bank_kind == "bank":
            return None
        if ACCUMULATED.search(name):
            return "accumulated"
        side = asset_side_by_number(number)
        if side == "current":
            return "current"
        if side == "non_current":
            return "fixed" if FIXED_ASSET.search(name) else "other"
        if FIXED_ASSET.search(name):
            return "fixed"
        return None
    if atype == "liability":
        if bank_kind != "credit_card" and LONG_TERM_DEBT.search(name):
            return "long_term"
    return None


def new_account_detail_type(account_type, name, number=None, bank_kind=None):
    """The detail type value the rule gives a NEW account created without
    one, or None where the type's default stands (2.28.0, the owner's call):
    an asset the rule calls non-current (accumulated depreciation, which
    #428 made a Fixed Asset, included) is a Fixed Asset, a liability it
    calls long-term debt a Long Term Liability."""
    where = placement(account_type, name, number, bank_kind)
    if where in ("accumulated", "fixed", "other"):
        return "fixed_asset"
    if where == "long_term":
        return "long_term_liability"
    return None


def backfill_detail_type(account_type, name, number=None, bank_kind=None):
    """The detail type value the migration's backfill gives an existing
    account by this rule, or None (its type's default). Finer than a new
    account's: a 1500-1999 asset not named like equipment is an Other Asset,
    as 2.27.0's statements had no Fixed / Other split to keep."""
    where = placement(account_type, name, number, bank_kind)
    return {
        "accumulated": "fixed_asset",
        "fixed": "fixed_asset",
        "other": "other_asset",
        "current": "other_current_asset",
        "long_term": "long_term_liability",
    }.get(where)
