"""Where the 2.26.0 cash flow statement placed an account, frozen (#390).

Before the account detail types, `cash_flow._place` guessed current from
non-current by the number range and the account's name. The statement now
reads the detail type, and the detail types migration (4ac01efbd72a) gave
every account the one that reproduces this placement. The tests that prove
an upgraded company's statement moves no figure compare against this copy;
the rules are the migration's own copies, which never change.
"""

import importlib.util
from pathlib import Path

from app.models.accounts import AccountType

ROOT = Path(__file__).resolve().parents[1]
REVISION = "4ac01efbd72a"


def migration():
    """The detail types revision as a module (its backfill and rules)."""
    path = ROOT / "migrations" / "versions" / f"{REVISION}_account_detail_types.py"
    spec = importlib.util.spec_from_file_location("detail_types_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RULES = migration()
_PL_TYPES = (AccountType.INCOME, AccountType.EXPENSE, AccountType.COGS)


def place_2_26(acct, fixed_ids: set, accumulated_ids: set) -> str:
    """app/services/cash_flow.py `_place` as 2.26.0 shipped it."""
    kind = acct.account_type
    if kind in _PL_TYPES:
        return "net_income"
    name = acct.name or ""
    number = (acct.account_number or "").strip()
    if kind == AccountType.ASSET:
        if acct.id in accumulated_ids or _RULES._ACCUMULATED.search(name):
            return "adjustments"
        if acct.id in fixed_ids:
            return "investing"
        if number.isdigit() and len(number) == 4 and number.startswith("1"):
            return "working_capital" if number < "1500" else "investing"
        if _RULES._FIXED_ASSET.search(name):
            return "investing"
        return "working_capital"
    if kind == AccountType.LIABILITY:
        if acct.bank_kind != "credit_card" and _RULES._LONG_TERM_DEBT.search(name):
            return "financing"
        return "working_capital"
    return "financing"
