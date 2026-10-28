# ============================================================================
# Phase 11: Inventory tracking with weighted-average cost.
#
# Design goals:
#   - Only items with Item.track_inventory=True flow through here. Services,
#     labor, and non-inventory items bypass these hooks entirely (so the old
#     invoice/bill posting semantics are unchanged for them).
#   - Every quantity change appends an InventoryMovement row. Qty and avg_cost
#     on the Item are denormalized caches kept in sync with the ledger.
#   - Weighted-average cost is recomputed on purchases:
#         new_avg = ((old_qty * old_avg) + (received_qty * received_cost))
#                   / (old_qty + received_qty)
#   - Sales post COGS at the current avg_cost (not the invoice rate, not the
#     item's stored cost). This is the standard perpetual-inventory approach
#     and keeps COGS honest when purchase costs move over time.
#   - Journal entries are:
#       Purchase (via bill):  DR Inventory Asset   CR Accounts Payable
#                             (the bill already posts the AP side; we add
#                              the inventory-side JE separately so services
#                              on the same bill still hit their expense accts)
#       Sale (via invoice):   DR COGS              CR Inventory Asset
# ============================================================================

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.items import (
    TRACKABLE_ITEM_TYPES,
    InventoryMovement,
    Item,
    MovementType,
)
from app.models.accounts import Account, AccountType
from app.services.accounting import _q, create_journal_entry, quantize_to

logger = logging.getLogger(__name__)

COST_Q = Decimal("0.0001")
# The memo of a reversal piece that matched no sale posting (a legacy or
# hand-made ledger): it names nothing, and must not count as pre-release
# unattributed quantity on a later reversal either.
_BEYOND_POSTINGS_MEMO = "Sale reversal (beyond postings)"


def get_inventory_asset_account_id(db: Session, item: Item) -> Optional[int]:
    """Resolve the inventory asset account for an item.

    Priority: item.asset_account_id → account #1300 (seeded "Inventory") → None.
    """
    if item.asset_account_id:
        return item.asset_account_id
    acc = db.query(Account).filter(Account.account_number == "1300").first()
    return acc.id if acc else None


def get_cogs_account_id(db: Session, item: Optional[Item] = None) -> Optional[int]:
    """Resolve the COGS account for an item's sale.

    Priority: the item's own Expense or COGS Account when it is COGS-typed
    (#622: it used to be ignored, so every sale landed on the first COGS
    account, 5000, and the P&L showed "Direct postings to Cost of Goods Sold"
    instead of the sub-account) → the first COGS-typed account → #5000.
    An item pointed at an expense-typed account keeps the fallback: a sale's
    cost side belongs in cost of goods, not operating expense. An account the
    user has made inactive is not reopened by a sale either.
    """
    if item is not None and item.expense_account_id:
        own = db.get(Account, item.expense_account_id)
        if own is not None and own.account_type == AccountType.COGS and own.is_active:
            return own.id
    acc = (
        db.query(Account)
        .filter(Account.account_type == AccountType.COGS)
        .order_by(Account.account_number)
        .first()
    )
    if acc:
        return acc.id
    acc = db.query(Account).filter(Account.account_number == "5000").first()
    return acc.id if acc else None


def _sale_entry_accounts(db: Session, movement: InventoryMovement) -> tuple:
    """(cogs_account_id, asset_account_id) of the entry a sale movement
    posted: the COGS-typed debit line and the asset-typed credit line. Either
    is None when the movement posted no entry or the line is not there."""
    if not movement.transaction_id:
        return None, None
    from app.models.transactions import TransactionLine

    rows = (
        db.query(
            TransactionLine.account_id, TransactionLine.debit, Account.account_type
        )
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(TransactionLine.transaction_id == movement.transaction_id)
        .all()
    )
    cogs_id = next(
        (a for a, debit, t in rows if debit and debit > 0 and t == AccountType.COGS),
        None,
    )
    asset_id = next(
        (
            a
            for a, debit, t in rows
            if not (debit and debit > 0) and t == AccountType.ASSET
        ),
        None,
    )
    return cogs_id, asset_id


def _append_movement(
    db: Session,
    item: Item,
    movement_type: MovementType,
    quantity: Decimal,
    unit_cost: Decimal,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    transaction_id: Optional[int] = None,
    memo: Optional[str] = None,
    reverses_movement_id: Optional[int] = None,
) -> InventoryMovement:
    """Write one movement row and update the denormalized Item cache.

    Caller is responsible for posting any corresponding journal entries.
    """
    # Update running balances BEFORE writing the row so balance_* reflects
    # the post-movement state (standard ledger convention).
    old_qty = Decimal(str(item.quantity_on_hand or 0))
    old_avg = Decimal(str(item.avg_cost or 0))
    new_qty = old_qty + quantity

    # Weighted average cost only changes on positive movements (receipts).
    # Sales, returns-out, and adjustments-out leave avg_cost unchanged —
    # you don't revalue remaining stock when you ship it.
    if quantity > 0 and new_qty > 0:
        new_avg = quantize_to(
            ((old_qty * old_avg) + (quantity * unit_cost)) / new_qty,
            COST_Q,
        )
    elif new_qty == 0:
        new_avg = Decimal("0")
    else:
        new_avg = old_avg

    item.quantity_on_hand = new_qty
    item.avg_cost = new_avg

    mv = InventoryMovement(
        item_id=item.id,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        balance_qty=new_qty,
        balance_avg_cost=new_avg,
        source_type=source_type,
        source_id=source_id,
        transaction_id=transaction_id,
        memo=memo,
        reverses_movement_id=reverses_movement_id,
    )
    db.add(mv)
    db.flush()
    return mv


def record_purchase(
    db: Session,
    item: Item,
    quantity: Decimal,
    unit_cost: Decimal,
    source_type: str,
    source_id: int,
    memo: Optional[str] = None,
    post_journal: bool = True,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Record an inventory receipt. Posts DR Inventory / CR Accounts Payable
    when post_journal=True. Callers that already include the inventory line
    in their own balanced JE should pass post_journal=False.

    Returns the movement row (or None if the item isn't inventory-tracked).
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    unit_cost = Decimal(str(unit_cost))
    if quantity <= 0:
        return None

    txn_id = None
    if post_journal:
        asset_id = get_inventory_asset_account_id(db, item)
        ap_acc = db.query(Account).filter(Account.account_number == "2000").first()
        if asset_id and ap_acc:
            from datetime import date as _date

            lines = [
                {
                    "account_id": asset_id,
                    "debit": _q(quantity * unit_cost),
                    "credit": Decimal("0"),
                    "description": f"Inventory: {item.name}",
                },
                {
                    "account_id": ap_acc.id,
                    "debit": Decimal("0"),
                    "credit": _q(quantity * unit_cost),
                    "description": f"A/P: {item.name}",
                },
            ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"Inventory receipt — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    return _append_movement(
        db,
        item,
        MovementType.PURCHASE,
        quantity=quantity,
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        transaction_id=txn_id,
        memo=memo,
    )


def record_opening_quantity(
    db: Session,
    item: Item,
    quantity: Decimal,
    unit_cost: Decimal,
    as_of=None,
) -> Optional[InventoryMovement]:
    """The stock an item starts with, as QuickBooks books it: one movement
    of `quantity` at `unit_cost`, and DR Inventory (the item's asset
    account, else 1300) / CR 3900 Opening Balance Equity for qty x cost,
    dated `as_of` (today when not given), source_type "item_opening" (its
    own type, so a fixed asset with the same id is not mistaken for it).

    A zero cost moves the stock with no journal entry: there is no value
    to carry. An item that already has movements gets nothing, so the
    opening quantity cannot post twice. Only a stock item posts (a service
    or labor item with the flag forced on does not). The closing date is
    enforced by create_journal_entry. Returns the movement, or None when
    nothing posted.
    """
    if not item.track_inventory or item.item_type not in TRACKABLE_ITEM_TYPES:
        return None
    quantity = Decimal(str(quantity or 0))
    unit_cost = Decimal(str(unit_cost or 0))
    if quantity <= 0:
        return None
    if item.id is not None and (
        db.query(InventoryMovement.id)
        .filter(InventoryMovement.item_id == item.id)
        .first()
        is not None
    ):
        return None

    from datetime import date as _date

    from app.services import control_accounts
    from app.services.accounting import get_opening_balance_equity_id

    txn_id = None
    amount = _q(quantity * unit_cost)
    if amount > 0:
        asset_id = item.asset_account_id or control_accounts.resolve(db, "1300")
        txn = create_journal_entry(
            db,
            as_of or _date.today(),
            f"Opening quantity — {item.name}",
            [
                {
                    "account_id": asset_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": f"Inventory: {item.name}",
                },
                {
                    "account_id": get_opening_balance_equity_id(db),
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": f"Opening quantity: {item.name}",
                },
            ],
            source_type="item_opening",
            source_id=item.id,
        )
        txn_id = txn.id

    return _append_movement(
        db,
        item,
        MovementType.ADJUSTMENT,
        quantity=quantity,
        unit_cost=unit_cost,
        source_type="item_opening",
        source_id=item.id,
        transaction_id=txn_id,
        memo="Opening quantity",
    )


def record_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    memo: Optional[str] = None,
    txn_date=None,
    post_journal: bool = True,
) -> Optional[InventoryMovement]:
    """Record an inventory ship-out. Uses the current weighted-avg cost for
    the COGS journal entry (DR COGS / CR Inventory Asset).

    Returns the movement row (or None if the item isn't inventory-tracked).
    Allows negative on-hand balances — some businesses back-date bills after
    invoicing. Watch your low-stock report.

    `post_journal=False` moves the stock without the COGS entry, for a sale
    whose cost is already in the books another way (a QuickBooks Online sale
    the QBO ledger import has posted, cost of goods included).
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    unit_cost = Decimal(str(item.avg_cost or 0))
    cogs_amount = _q(quantity * unit_cost)

    txn_id = None
    if cogs_amount > 0 and post_journal:
        asset_id = get_inventory_asset_account_id(db, item)
        cogs_id = get_cogs_account_id(db, item)
        if asset_id and cogs_id:
            from datetime import date as _date

            lines = [
                {
                    "account_id": cogs_id,
                    "debit": cogs_amount,
                    "credit": Decimal("0"),
                    "description": f"COGS: {item.name}",
                },
                {
                    "account_id": asset_id,
                    "debit": Decimal("0"),
                    "credit": cogs_amount,
                    "description": f"Inventory: {item.name}",
                },
            ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"COGS — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    return _append_movement(
        db,
        item,
        MovementType.SALE,
        quantity=-quantity,  # store as negative
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        transaction_id=txn_id,
        memo=memo,
    )


def record_adjustment(
    db: Session,
    item: Item,
    quantity_delta: Decimal,
    unit_cost: Optional[Decimal] = None,
    memo: Optional[str] = None,
    post_journal: bool = True,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Manual inventory adjustment (count correction, shrinkage, etc.).

    quantity_delta can be positive (increase stock) or negative (decrease).
    If post_journal=True, books a one-sided JE to an "Inventory Adjustment"
    expense account if #5900 exists, otherwise to COGS.
    """
    if not item.track_inventory:
        return None

    quantity_delta = Decimal(str(quantity_delta))
    if quantity_delta == 0:
        return None

    if unit_cost is None:
        unit_cost = Decimal(str(item.avg_cost or 0))
    else:
        unit_cost = Decimal(str(unit_cost))

    amount = _q(abs(quantity_delta) * unit_cost)
    txn_id = None

    if post_journal and amount > 0:
        asset_id = get_inventory_asset_account_id(db, item)
        adj_acc = db.query(Account).filter(Account.account_number == "5900").first()
        # An adjustment is not a sale; its offset stays 5900, else the chart's
        # first COGS account, as before (#622 changes sales and their reversals).
        offset_id = adj_acc.id if adj_acc else get_cogs_account_id(db)
        if asset_id and offset_id:
            from datetime import date as _date

            if quantity_delta > 0:
                # stock up: DR Inventory / CR Adjustment
                lines = [
                    {
                        "account_id": asset_id,
                        "debit": amount,
                        "credit": Decimal("0"),
                        "description": f"Inventory adj: {item.name}",
                    },
                    {
                        "account_id": offset_id,
                        "debit": Decimal("0"),
                        "credit": amount,
                        "description": "Inventory adjustment gain",
                    },
                ]
            else:
                # stock down: DR Adjustment / CR Inventory
                lines = [
                    {
                        "account_id": offset_id,
                        "debit": amount,
                        "credit": Decimal("0"),
                        "description": "Inventory adjustment loss",
                    },
                    {
                        "account_id": asset_id,
                        "debit": Decimal("0"),
                        "credit": amount,
                        "description": f"Inventory adj: {item.name}",
                    },
                ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"Inventory adjustment — {item.name}",
                lines,
                source_type="adjustment",
                source_id=item.id,
            )
            txn_id = txn.id

    return _append_movement(
        db,
        item,
        MovementType.ADJUSTMENT,
        quantity=quantity_delta,
        unit_cost=unit_cost,
        source_type="adjustment",
        source_id=item.id,
        transaction_id=txn_id,
        memo=memo,
    )


def reverse_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    original_source_type: Optional[str] = None,
    original_source_id: Optional[int] = None,
    txn_date=None,
    post_journal: bool = True,
) -> Optional[InventoryMovement]:
    """Reverse a previous sale (invoice void).

    `post_journal=False` returns the stock without the cost entry, for a
    sale whose cost was carried by a posting being reversed with it (a
    QuickBooks Online sale's import posting).

    CRITICAL: we must reverse at the ORIGINAL sale's unit_cost, not the
    current avg_cost. If cost moved between sale and void, using the
    current cost would leave a permanent GL imbalance (the original COGS
    debit wouldn't match the reversal credit).

    Looks up the original SALE movement by (source_type, source_id) and
    uses its unit_cost. Falls back to current avg_cost if we can't find
    the original (legacy rows, manual corrections) but logs the fact.
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    # The reversal comes off where the sale went on. An invoice can hold more
    # than one SALE movement for the item (each quantity-up edit adds one)
    # and, since #622, they can sit on different accounts at different costs.
    # Each posting's remainder is what earlier reversals that name it left;
    # reversals from before this release name nothing, so their total is
    # taken off the newest postings first (the order they were made in).
    # Walk the postings newest first, one reversal movement per piece, at the
    # piece's own cost, against the piece's own accounts (sb-qa). With no
    # original to match: current avg_cost and the chart's accounts, as before.
    pieces = (
        []
    )  # (quantity, unit_cost, cogs_account_id, asset_account_id, sale movement)
    remaining = quantity
    originals = []
    if original_source_type and original_source_id:
        originals = (
            db.query(InventoryMovement)
            .filter(
                InventoryMovement.item_id == item.id,
                InventoryMovement.source_type == original_source_type,
                InventoryMovement.source_id == original_source_id,
                InventoryMovement.movement_type == MovementType.SALE,
            )
            .order_by(InventoryMovement.id.desc())
            .all()
        )
        void_q = db.query(
            func.coalesce(func.sum(InventoryMovement.quantity), 0)
        ).filter(
            InventoryMovement.item_id == item.id,
            InventoryMovement.movement_type == MovementType.VOID,
        )
        unattributed = Decimal(
            str(
                void_q.filter(
                    InventoryMovement.source_id == original_source_id,
                    InventoryMovement.source_type.in_(
                        (f"{original_source_type}_void", f"{original_source_type}_edit")
                    ),
                    InventoryMovement.reverses_movement_id.is_(None),
                    or_(
                        InventoryMovement.memo.is_(None),
                        InventoryMovement.memo != _BEYOND_POSTINGS_MEMO,
                    ),
                ).scalar()
                or 0
            )
        )
        linked_taken = {
            mv.id: Decimal(
                str(
                    void_q.filter(
                        InventoryMovement.reverses_movement_id == mv.id
                    ).scalar()
                    or 0
                )
            )
            for mv in originals
        }
        # A reversal from before the link existed predates every posting made
        # after it, so it can only have consumed the postings of its day:
        # allocate it oldest-first (sb-qa). Postings were all on 5000 back
        # then, so for an install with no newer posting nothing changes.
        legacy_taken = {}
        for mv in reversed(originals):
            room = abs(Decimal(str(mv.quantity))) - linked_taken[mv.id]
            legacy_taken[mv.id] = max(min(room, unattributed), Decimal("0"))
            unattributed -= legacy_taken[mv.id]
        for mv in originals:
            if remaining <= 0:
                break
            available = (
                abs(Decimal(str(mv.quantity)))
                - linked_taken[mv.id]
                - legacy_taken[mv.id]
            )
            if available <= 0:
                continue
            take = min(available, remaining)
            cogs_id, asset_id = _sale_entry_accounts(db, mv)
            pieces.append(
                (take, Decimal(str(mv.unit_cost or 0)), cogs_id, asset_id, mv)
            )
            remaining -= take
    if remaining > 0:
        if originals:
            logger.warning(
                "reverse_sale: %s of %s for item %s exceeds its sale postings; "
                "the remainder is reversed at avg_cost",
                remaining,
                original_source_type,
                item.id,
            )
        pieces.append((remaining, Decimal(str(item.avg_cost or 0)), None, None, None))

    txn_id = None
    if post_journal:
        fallback_asset = get_inventory_asset_account_id(db, item)
        # a sale that posted no entry could only have gone to the chart's
        # account, never the item's, so the fallback is item-less
        fallback_cogs = get_cogs_account_id(db)
        by_accounts = {}
        for q, c, cogs_id, asset_id, _mv in pieces:
            key = (asset_id or fallback_asset, cogs_id or fallback_cogs)
            by_accounts[key] = by_accounts.get(key, Decimal("0")) + _q(q * c)
        lines = []
        for (asset_id, cogs_id), piece_amount in by_accounts.items():
            if piece_amount <= 0:
                continue
            if not (asset_id and cogs_id):
                logger.warning(
                    "reverse_sale: no inventory or COGS account for a %s piece of "
                    "item %s; its stock is reversed but no entry is posted for it",
                    piece_amount,
                    item.id,
                )
                continue
            lines.append(
                {
                    "account_id": asset_id,
                    "debit": piece_amount,
                    "credit": Decimal("0"),
                    "description": f"Inventory reversal: {item.name}",
                }
            )
            lines.append(
                {
                    "account_id": cogs_id,
                    "debit": Decimal("0"),
                    "credit": piece_amount,
                    "description": f"COGS reversal: {item.name}",
                }
            )
        if lines:
            from datetime import date as _date

            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"COGS reversal — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    last = None
    for q, c, _cogs, _asset, mv in pieces:
        last = _append_movement(
            db,
            item,
            MovementType.VOID,
            quantity=q,
            unit_cost=c,
            source_type=source_type,
            source_id=source_id,
            transaction_id=txn_id,
            memo="Sale reversal" if mv is not None else _BEYOND_POSTINGS_MEMO,
            reverses_movement_id=mv.id if mv is not None else None,
        )
    return last


def current_valuation(db: Session) -> dict:
    """Return aggregate inventory valuation: total_value, item_count, low_stock_count."""
    items = db.query(Item).filter(Item.track_inventory, Item.is_active).all()  # noqa
    total_value = Decimal("0")
    item_count = 0
    low_stock = 0
    for it in items:
        qty = Decimal(str(it.quantity_on_hand or 0))
        avg = Decimal(str(it.avg_cost or 0))
        total_value += qty * avg
        item_count += 1
        if Decimal(str(it.reorder_point or 0)) > 0 and qty <= Decimal(
            str(it.reorder_point)
        ):
            low_stock += 1
    return {
        "total_value": float(_q(total_value)),
        "item_count": item_count,
        "low_stock_count": low_stock,
    }
