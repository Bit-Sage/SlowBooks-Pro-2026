"""Entity-aware equity accounts (#488, step 2).

The chart is seeded when the company file is made, before anyone has said
what the company is. When the entity type is chosen — in the setup wizard
or under Settings → Company — the equity block follows it, but only while
the chart is still the untouched seed and the books hold no posting. After
that nothing is reseeded: the chart is the operator's, and the Settings page
says what to rename instead.
"""

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction
from app.seed.chart_of_accounts import EQUITY_SEEDS, equity_family

# The equity accounts no family owns: Retained Earnings (a control account,
# in every block) and Opening Balance Equity (created on demand).
_SHARED = frozenset({"3200", "3900"})

# What each family calls its draw account, for the UI (#557): a corporation
# says Shareholder Distributions, never Owner's Draw.
DISTRIBUTIONS_LABEL = {
    "sole_proprietor": "Owner's Draw",
    "partnership": "Partner Distributions",
    "corporation": "Shareholder Distributions",
}


def _equity_accounts(db: Session) -> list[Account]:
    return (
        db.query(Account)
        .filter(Account.account_type == AccountType.EQUITY)
        .filter(~Account.account_number.in_(list(_SHARED)))
        .all()
    )


def current_family(db: Session) -> str:
    """The family whose seed the chart's equity block still is, exactly
    (numbers, names, all system accounts), or "" once it has been edited."""
    present = {(a.account_number, a.name) for a in _equity_accounts(db)}
    if not present or any(not a.is_system for a in _equity_accounts(db)):
        return ""
    for family, seed in EQUITY_SEEDS.items():
        if present == {(n, name) for n, name in seed if n not in _SHARED}:
            return family
    return ""


def apply_entity_equity(db: Session, info: dict) -> dict:
    """Make the equity block match the entity's family; flushes, the caller
    commits. Returns {"family", "applied", "accounts", "note"}: ``applied``
    is whether anything changed, ``accounts`` the family's block as the
    chart now has it, ``note`` one sentence for the person when it did not
    follow (the books have postings, or the chart has been edited).

    Never touches 3200 or 3900, never runs in nonprofit mode, and never
    deletes an account with a journal line: the swap only happens while the
    company has no posting at all, so the accounts it renames or removes
    have never been used.
    """
    family = equity_family(info.get("return_form"))
    out = {"family": family, "applied": False, "accounts": [], "note": ""}
    if not family or info.get("nonprofit"):
        return out
    want = [(n, name) for n, name in EQUITY_SEEDS[family]]
    out["accounts"] = [f"{n} {name}" for n, name in want]
    have = current_family(db)
    if have == family:
        return out
    if db.query(Transaction.id).first() is not None:
        out["note"] = (
            "The books already have postings, so the equity accounts were "
            "left as they are. Rename or add them under Chart of Accounts: "
            + ", ".join(out["accounts"][:-1])
            + "."
        )
        return out
    if not have:
        out["note"] = (
            "The equity accounts have been edited, so they were left as they "
            "are. Rename or add them under Chart of Accounts: "
            + ", ".join(out["accounts"][:-1])
            + "."
        )
        return out
    # The chart is a pristine seed and nothing has posted: rename in place
    # where the number is shared (3000, 3100 keep their ids), add what the
    # family has more of, and remove what it has fewer of.
    by_number = {a.account_number: a for a in _equity_accounts(db)}
    wanted = {n: name for n, name in want if n not in _SHARED}
    for number, name in wanted.items():
        acct = by_number.get(number)
        if acct is not None:
            acct.name = name
        else:
            db.add(
                Account(
                    account_number=number,
                    name=name,
                    account_type=AccountType.EQUITY,
                    is_system=True,
                )
            )
    for number, acct in by_number.items():
        if number not in wanted:
            db.delete(acct)
    db.flush()
    out["applied"] = True
    return out
