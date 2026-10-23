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
