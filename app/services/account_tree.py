"""Walking the chart's parent links (Testing #101).

A parent loop (A under B, B under A) left every walker of the tree —
the report rows' "Parent: Child" paths, the IIF export's full names —
going round forever. The API and the chart importer refuse one here; the
walkers also stop at a repeat, for a loop already saved.

``forms_cycle`` is the one walk, shared with the cost-code tree
(``job_costing.would_cycle``): the same logic over a different model.
"""

from sqlalchemy.orm import Session

from app.models.accounts import Account

# Deeper than any real chart. A chain this long is bad data already saved
# (a loop the walk has not closed yet), so the link is refused, not allowed.
MAX_DEPTH = 64


def forms_cycle(db: Session, model, child_id, parent_id) -> bool:
    """Would making ``parent_id`` the parent of ``child_id`` close a loop?
    Walk up the parent chain from ``parent_id``; a loop forms if it reaches
    ``child_id``, revisits a node (a loop already in the data), or runs past
    ``MAX_DEPTH``. Reads each row from the session, so links set but not yet
    flushed count."""
    if child_id is None or parent_id is None:
        return False
    cur, seen = parent_id, set()
    while cur is not None:
        if cur == child_id:
            return True
        if cur in seen or len(seen) >= MAX_DEPTH:
            return True  # a loop already saved, or a chain too deep to be real
        seen.add(cur)
        row = db.get(model, cur)
        cur = row.parent_id if row else None
    return False


def loop_through(db: Session, account_id, parent_id):
    """The proposed parent account, when putting ``account_id`` under
    ``parent_id`` would make the account a sub-account of itself — directly,
    or through a chain already looped or impossibly deep; else None."""
    if forms_cycle(db, Account, account_id, parent_id):
        return db.get(Account, parent_id)
    return None


def loop_message(account_name: str, parent_name: str) -> str:
    return (
        f"That would make {account_name} a sub-account of itself "
        f"(through {parent_name})."
    )


def chain_depth(db: Session, model, account_id) -> int:
    """How many accounts from this one up to the root, counting itself
    (#416). Stops at a repeat or MAX_DEPTH so a loop or a chain already too
    deep in the data cannot run away."""
    cur, seen, depth = account_id, set(), 0
    while cur is not None and cur not in seen and depth < MAX_DEPTH:
        seen.add(cur)
        depth += 1
        row = db.get(model, cur)
        cur = row.parent_id if row else None
    return depth


def depth_message(name: str, depth: int) -> str:
    return f"{name} would be {depth} levels deep; the chart stops at {MAX_DEPTH}."


# "must be an asset too": a base type in a sentence
BASE_WORDS = {
    "asset": "an asset",
    "liability": "a liability",
    "equity": "an equity account",
    "income": "an income account",
    "cogs": "a cost of goods sold account",
    "expense": "an expense",
}


def _value(v):
    return getattr(v, "value", v)


def parent_link_problem(
    parent_name: str,
    parent_type,
    parent_detail,
    child_type,
    child_detail=None,
    child_bank_kind=None,
    child_number=None,
) -> str | None:
    """Why an importer must leave a parent link off, or None (#416): the
    pairs the account API refuses. A sub-account shares its parent's base
    type; under any parent but 1100, 2000, a bank or a card it also takes
    the parent's detail type, so one that cannot carry it (a bank account
    under a plain folder) cannot go there. A declared detail type that
    *can* follow is not refused here; the child takes the parent's instead
    (``inherited_detail_type``, #390)."""
    from app.models.accounts import (
        PINNED_DETAIL_TYPES,
        detail_label,
        detail_type_problem,
    )

    want = _value(parent_type)
    if _value(child_type) != want:
        return f"a sub-account of {parent_name} must be {BASE_WORDS[want]} too"
    detail = _value(parent_detail)
    if not detail or detail in PINNED_DETAIL_TYPES:
        return None
    if _value(child_detail) == detail:
        return None
    try:
        why = detail_type_problem(detail, child_type, child_bank_kind, child_number)
    except ValueError:
        why = None
    if why:
        return f"a sub-account of {parent_name} ({detail_label(detail)}) cannot be one: {why}"
    return None


def inherited_detail_type(parent_detail, child_detail) -> str | None:
    """The detail type a sub-account must take from its parent, or None
    (#390). A parent other than a pinned one (1100, 2000, a bank, a card)
    hands its detail type down; the account API sets it, so the importers
    must too, even when the file declared a different type that would fit on
    its own. Call this only for a link that stands (``parent_link_problem``
    returned None), so the child can carry what comes down."""
    from app.models.accounts import PINNED_DETAIL_TYPES

    parent = _value(parent_detail)
    if not parent or parent in PINNED_DETAIL_TYPES:
        return None
    if _value(child_detail) == parent:
        return None
    return parent


def takes_no_new_parent(detail, number) -> bool:
    """A control account, or an Accounts Receivable / Payable one, is found
    by its number and stands alone: it takes no new parent (#416)."""
    from app.services import control_accounts

    return _value(detail) in (
        "accounts_receivable",
        "accounts_payable",
    ) or control_accounts.is_control_number(number)
