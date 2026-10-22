"""Walking the chart's parent links (Testing #101).

A parent loop (A under B, B under A) left every walker of the tree —
the report rows' "Parent: Child" paths, the IIF export's full names —
going round forever. The edit form and the chart importer refuse one
here; the walkers also stop at a repeat, for a loop already saved.
"""

from sqlalchemy.orm import Session

from app.models.accounts import Account

# Deeper than any real chart; a walk stops here even on bad data.
MAX_DEPTH = 64


def loop_through(db: Session, account_id, parent_id):
    """The proposed parent, when putting ``account_id`` under ``parent_id``
    would make the account a sub-account of itself; else None. Reads each
    parent from the session, so links set but not yet flushed count."""
    if account_id is None or parent_id is None:
        return None
    proposed = db.get(Account, parent_id)
    cur, seen = proposed, set()
    while cur is not None and len(seen) < MAX_DEPTH:
        if cur.id == account_id:
            return proposed
        if cur.id in seen:  # a loop already saved, not through this account
            return None
        seen.add(cur.id)
        cur = db.get(Account, cur.parent_id) if cur.parent_id else None
    return None


def loop_message(account_name: str, parent_name: str) -> str:
    return (
        f"That would make {account_name} a sub-account of itself "
        f"(through {parent_name})."
    )
