# ============================================================================
# Phase 11 (post-audit fix): Centralized inventory hooks.
#
# Every path that creates or modifies an invoice/bill/credit-memo goes through
# these helpers so nobody forgets to post the inventory movement + COGS JE.
#
# Callers: invoices.create/update/void/duplicate, estimates.convert_to_invoice,
# credit_memos.create/void, recurring_service.generate_due_invoices, iif_import,
# bills.create/void.
#
# Design rule: all helpers are NO-OPS if track_inventory=False on the item,
# so they're safe to call unconditionally for every line.
# ============================================================================

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.items import InventoryMovement, Item, MovementType
from app.models.transactions import Transaction
from app.services.accounting import _q, create_journal_entry, reversing_lines
from app.services.inventory_service import (
    record_sale,
    return_cost_accounts,
    reverse_sale,
    sale_pieces,
    _append_movement,
)

logger = logging.getLogger(__name__)


def _get_item(db: Session, item_id: Optional[int]) -> Optional[Item]:
    if not item_id:
        return None
    return db.query(Item).filter(Item.id == item_id).first()


def post_sale_for_invoice(
    db: Session, invoice, txn_date=None, post_journal: bool = True
) -> None:
    """Iterate an invoice's lines and record_sale for every inventory item.

    Safe to call on any invoice — non-inventory items are silently skipped.
    Uses invoice.date as the transaction date so the COGS posting lands in
    the same accounting period as the sale. `post_journal=False` moves the
    stock without the COGS entry (record_sale).
    """
    date_to_use = txn_date or invoice.date
    ref = getattr(invoice, "invoice_number", None) or f"inv#{invoice.id}"
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    terms = terms_from_db(db)
    for line in invoice.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            record_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice",
                source_id=invoice.id,
                memo=f"{document_label(invoice, terms)} {ref}",
                txn_date=date_to_use,
                post_journal=post_journal,
            )


def reverse_sale_for_invoice(
    db: Session,
    invoice,
    txn_date=None,
    memo: str = "Sale reversal",
) -> None:
    """Reverse every SALE movement originally booked against this invoice.

    Used by void_invoice and (via update_invoice) when an edit needs to
    recompute from scratch. Passes original_source to reverse_sale so
    historical unit_cost is used for the reversal JE — keeps the GL balanced
    even if avg_cost has drifted.
    """
    date_to_use = txn_date or invoice.date
    for line in invoice.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            reverse_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=date_to_use,
            )


def _memo_ref(credit_memo) -> str:
    return getattr(credit_memo, "memo_number", None) or f"cm#{credit_memo.id}"


def post_return_for_credit_memo(db: Session, credit_memo, txn_date=None) -> None:
    """A credit memo returns stock: each inventory line's quantity goes back
    on the shelf, and the cost of what came back goes back with it, one
    journal entry per memo, DR Inventory Asset / CR COGS (#641), which the
    return movements link to (transaction_id).

    The cost is the original sale's when the memo names an invoice: the
    quantity comes off that invoice's sale postings the way a void does
    (sale_pieces, newest first, earlier reversals and returns already
    taken off), each piece at the posting's own cost and back onto the
    accounts its entry used, with one RETURN_IN movement per piece naming
    the posting. What the postings do not cover, or every line of a memo
    naming no invoice, is at the item's current avg_cost onto the item's
    (else the chart's) accounts, the cost the return already used for the
    quantity. The same item on two lines returns their total. A line whose
    item is not tracked, or of zero quantity, moves and posts nothing.
    """
    date_to_use = txn_date or credit_memo.date
    ref = _memo_ref(credit_memo)
    invoice_id = getattr(credit_memo, "original_invoice_id", None)

    wanted: dict = {}  # item_id -> [item, quantity], in line order
    for line in credit_memo.lines:
        item = _get_item(db, line.item_id)
        quantity = Decimal(str(line.quantity or 0))
        if not (item and item.track_inventory) or quantity <= 0:
            continue
        wanted.setdefault(item.id, [item, Decimal("0")])[1] += quantity

    returns = []  # (item, piece)
    for item, quantity in wanted.values():
        returns.extend(
            (item, piece)
            for piece in sale_pieces(
                db,
                item,
                quantity,
                "invoice" if invoice_id else None,
                invoice_id,
                count_returns=True,
            )
        )
    if not returns:
        return

    # One debit/credit pair per item and pair of accounts, in return order.
    amounts: dict = {}
    for item, piece in returns:
        amount = _q(piece[0] * piece[1])
        if amount <= 0:
            continue
        asset_id, cogs_id = return_cost_accounts(db, item, piece)
        if not (asset_id and cogs_id):
            logger.warning(
                "credit memo %s: no inventory or COGS account for a %s return of "
                "item %s; its stock is returned but no entry is posted for it",
                ref,
                amount,
                item.id,
            )
            continue
        key = (item.id, item.name, asset_id, cogs_id)
        amounts[key] = amounts.get(key, Decimal("0")) + amount
    lines = []
    for (_item_id, name, asset_id, cogs_id), amount in amounts.items():
        lines.append(
            {
                "account_id": asset_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"Inventory returned: {name}",
            }
        )
        lines.append(
            {
                "account_id": cogs_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"COGS returned: {name}",
            }
        )
    txn_id = None
    if lines:
        txn = create_journal_entry(
            db,
            date_to_use,
            f"Credit Memo {ref} - returned stock",
            lines,
            source_type="credit_memo",
            source_id=credit_memo.id,
        )
        txn_id = txn.id

    for item, (quantity, unit_cost, _cogs, _asset, sale) in returns:
        _append_movement(
            db,
            item,
            MovementType.RETURN_IN,
            quantity=quantity,
            unit_cost=unit_cost,
            source_type="credit_memo",
            source_id=credit_memo.id,
            transaction_id=txn_id,
            memo=f"Return: {ref}",
            reverses_movement_id=sale.id if sale is not None else None,
        )


def reverse_return_for_credit_memo(db: Session, credit_memo, txn_date=None) -> None:
    """Void a credit memo's return (#641): the stock it put back goes out
    again and its cost entry is reversed, mirror-image. Every RETURN_IN
    movement the memo made gets a VOID movement of the same quantity at the
    same cost naming it (so the ledger nets to zero at the return's cost,
    as a sale's void does), and the entry the returns link to is reversed
    line for line under "credit_memo_void". A memo from before the entry
    existed only has its stock taken back out. An item no longer tracked
    is left alone, as a sale's reversal leaves it.
    """
    date_to_use = txn_date or credit_memo.date
    ref = _memo_ref(credit_memo)
    returns = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "credit_memo",
            InventoryMovement.source_id == credit_memo.id,
            InventoryMovement.movement_type == MovementType.RETURN_IN,
        )
        .order_by(InventoryMovement.id)
        .all()
    )
    reversal_of: dict = {}  # return entry id -> reversing entry id
    for mv in returns:
        if not mv.transaction_id or mv.transaction_id in reversal_of:
            continue
        reversal_of[mv.transaction_id] = None
        txn = db.get(Transaction, mv.transaction_id)
        if txn is None:
            continue
        reversal_of[mv.transaction_id] = create_journal_entry(
            db,
            date_to_use,
            f"VOID Credit Memo {ref} - returned stock",
            reversing_lines(txn.lines),
            source_type="credit_memo_void",
            source_id=credit_memo.id,
        ).id
    for mv in returns:
        item = mv.item
        quantity = Decimal(str(mv.quantity or 0))
        if not (item and item.track_inventory) or quantity <= 0:
            continue
        _append_movement(
            db,
            item,
            MovementType.VOID,
            quantity=-quantity,
            unit_cost=Decimal(str(mv.unit_cost or 0)),
            source_type="credit_memo_void",
            source_id=credit_memo.id,
            transaction_id=reversal_of.get(mv.transaction_id),
            memo=f"VOID Credit Memo {ref}",
            reverses_movement_id=mv.id,
        )


# ---------------------------------------------------------------------------
# Diff-based reconciliation for PUT /api/invoices/{id}
# ---------------------------------------------------------------------------


def reconcile_invoice_inventory_delta(
    db: Session,
    invoice,
    old_line_snapshot: list[dict[str, Any]],
    txn_date=None,
    post_journal: bool = True,
) -> None:
    """After an invoice edit that replaced all InvoiceLines, compare the
    old snapshot against the new lines and post compensating movements:

      - If an item's qty increased by N (or a new line was added): record_sale(N)
      - If an item's qty decreased by N (or a line was removed):    reverse_sale(N)

    This is the ONLY correct way to keep the inventory ledger consistent
    with user edits without a full void+recreate.

    `old_line_snapshot` is a list of {"item_id": int, "quantity": Decimal}
    captured BEFORE the route's .delete() + rebuild of InvoiceLines.
    """
    date_to_use = txn_date or invoice.date

    # Aggregate old qty per item_id
    old_qty: dict[int, Decimal] = {}
    for row in old_line_snapshot:
        item_id = row.get("item_id")
        if not item_id:
            continue
        old_qty[item_id] = old_qty.get(item_id, Decimal("0")) + Decimal(
            str(row["quantity"])
        )

    # Aggregate new qty per item_id
    new_qty: dict[int, Decimal] = {}
    for line in invoice.lines:
        if not line.item_id:
            continue
        new_qty[line.item_id] = new_qty.get(line.item_id, Decimal("0")) + Decimal(
            str(line.quantity)
        )

    all_items = set(old_qty) | set(new_qty)
    for item_id in all_items:
        item = _get_item(db, item_id)
        if not (item and item.track_inventory):
            continue
        old_q = old_qty.get(item_id, Decimal("0"))
        new_q = new_qty.get(item_id, Decimal("0"))
        delta = new_q - old_q
        if delta > 0:
            # Additional qty sold
            record_sale(
                db,
                item,
                quantity=delta,
                source_type="invoice",
                source_id=invoice.id,
                memo=f"Edit +{delta}",
                txn_date=date_to_use,
                post_journal=post_journal,
            )
        elif delta < 0:
            # Qty reduced — reverse the difference
            reverse_sale(
                db,
                item,
                quantity=-delta,
                source_type="invoice_edit",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=date_to_use,
                post_journal=post_journal,
            )


def snapshot_invoice_lines(invoice) -> list[dict[str, Any]]:
    """Helper: capture the (item_id, quantity) of each line BEFORE an edit
    rebuilds them. Used by reconcile_invoice_inventory_delta."""
    return [
        {"item_id": line.item_id, "quantity": Decimal(str(line.quantity))}
        for line in invoice.lines
    ]
