# ============================================================================
# Bank rule application — shared by every bank-feed importer (OFX, CSV,
# SimpleFIN) and the Bank Rules page.
#
# A rule SUGGESTS a category for a statement line that matches its payee
# pattern. It never changes the line's status and never posts: adding to
# the books is one explicit click ("Add" / "Add all categorised"), so
# nothing reaches the ledger silently (issue #114).
# ============================================================================

import logging

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction

logger = logging.getLogger(__name__)


def _matches(rule_type: str, pattern: str, payee: str) -> bool:
    pattern = (pattern or "").strip().lower()
    if not pattern:
        return False
    if rule_type == "contains":
        return pattern in payee
    if rule_type == "starts_with":
        return payee.startswith(pattern)
    if rule_type == "exact":
        return payee == pattern
    return False


def _hit(rule, payee: str, vendor_names: dict | None = None) -> bool:
    """The rule's pattern matches the payee (lower-cased) — or, for a rule
    that names a vendor (#493), the vendor's own name does, the same way:
    a vendor renamed after its rule was made is still found."""
    if _matches(rule.rule_type, rule.pattern, payee):
        return True
    if rule.vendor_id and vendor_names:
        return _matches(rule.rule_type, vendor_names.get(rule.vendor_id), payee)
    return False


def _active_rules(db: Session):
    from app.models.bank_rules import BankRule

    return (
        db.query(BankRule)
        .filter(BankRule.is_active)
        .order_by(BankRule.priority.desc(), BankRule.id)
        .all()
    )


def _vendor_names(db: Session, rules) -> dict:
    from app.models.contacts import Vendor

    ids = {r.vendor_id for r in rules if r.vendor_id}
    if not ids:
        return {}
    return {v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(ids))}


def rule_for(db: Session, payee: str | None):
    """The active rule a statement line with this payee follows (highest
    priority first, first hit wins), or None."""
    rules = _active_rules(db)
    names = _vendor_names(db, rules)
    text = (payee or "").lower()
    return next((r for r in rules if _hit(r, text, names)), None)


def apply_bank_rules(db: Session, bank_account_id: int | None = None) -> int:
    """Categorise unmatched statement lines by the active rules (highest
    priority first, first hit wins). Returns how many lines got a category.
    Commits when anything changed. The vendor a rule names is not kept on
    the line: Add asks the same rule again (bank_matching.add)."""
    rules = _active_rules(db)
    if not rules:
        return 0
    names = _vendor_names(db, rules)
    q = db.query(BankTransaction).filter(BankTransaction.match_status == "unmatched")
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    categorised = 0
    for txn in q.all():
        payee = (txn.payee or "").lower()
        for rule in rules:
            if _hit(rule, payee, names):
                if rule.account_id and txn.category_account_id != rule.account_id:
                    txn.category_account_id = rule.account_id
                    categorised += 1
                break
    if categorised:
        db.commit()
    return categorised
