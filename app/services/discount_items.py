# ============================================================================
# Items whose lines may carry a negative price (#639).
#
# A negative price is a discount. An item marked "Discount or concession"
# (items.allows_negative_price, the item form's box) takes one, and so does
# the item a QuickBooks Online discount came in on (qbo_common.discount_
# mapping), which the migration that added the flag marked as well. Any
# other item, or a line with no item, is refused (routes/invoices/helpers.py,
# refuse_negative_lines): a refund belongs on a credit memo. The items
# routes tell the sales forms which items these are (is_discount on an item
# response), so a line's price box takes a negative price on them.
#
# The ledger needs nothing more: a negative line is a debit to the item's
# income account on the accrual basis (routes/invoices/helpers.py,
# _build_invoice_journal_lines) and keeps its sign when a payment recognizes
# it on the cash basis (services/cash_basis.py, prorate).
# ============================================================================

from sqlalchemy.orm import Session

from app.models.items import Item
from app.services.qbo_common import discount_item_ids, is_discount_item


def takes_negative_price(db: Session, item_id) -> bool:
    """Whether a line on this item may carry a negative price: the item is
    marked as a discount or concession, or a QuickBooks Online discount came
    in on it. False with no item."""
    if not item_id:
        return False
    item = db.get(Item, item_id)
    if item is not None and item.allows_negative_price:
        return True
    return is_discount_item(db, item_id)


def negative_price_item_ids(db: Session) -> set[int]:
    """Every item a line may carry a negative price on."""
    flagged = {
        item_id
        for (item_id,) in db.query(Item.id).filter(Item.allows_negative_price.is_(True))
    }
    return flagged | discount_item_ids(db)
