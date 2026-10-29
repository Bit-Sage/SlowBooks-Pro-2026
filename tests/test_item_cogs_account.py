"""An inventory sale posts its cost to the item's own COGS account (#622,
from Discussion #621).

It used to go to the first COGS-typed account in the chart (5000, the
parent) whatever the item said, so the P&L listed it as "Direct postings to
Cost of Goods Sold" rather than under the sub-account the item names."""

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.items import Item, ItemType, InventoryMovement, MovementType
from app.models.transactions import TransactionLine
from app.services.inventory_service import get_cogs_account_id

from tests.test_inventory import (
    _create_bill_for_inventory,
    _create_invoice_for_inventory,
    _seed_vendor,
)


def _sub_cogs(db, accounts, number="5150", name="Cost of Widgets"):
    acc = Account(
        account_number=number,
        name=name,
        account_type=AccountType.COGS,
        parent_id=accounts["5000"].id,
        balance=Decimal("0"),
    )
    db.add(acc)
    db.commit()
    return acc


def _item(db, accounts, expense_account_id):
    it = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25.00"),
        cost=Decimal("10.00"),
        income_account_id=accounts["4000"].id,
        expense_account_id=expense_account_id,
        asset_account_id=accounts["1300"].id,
        track_inventory=True,
        is_taxable=False,
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _cogs_lines(db, item, movement_type):
    mv = (
        db.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=movement_type)
        .order_by(InventoryMovement.id.desc())
        .first()
    )
    assert mv is not None and mv.transaction_id is not None
    return (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == mv.transaction_id)
        .all()
    )


def test_the_resolver_prefers_the_items_cogs_account(db_session, seed_accounts):
    sub = _sub_cogs(db_session, seed_accounts)
    assert (
        get_cogs_account_id(db_session, _item(db_session, seed_accounts, sub.id))
        == sub.id
    )


def test_the_resolver_falls_back_without_an_item_or_account(db_session, seed_accounts):
    first = seed_accounts["5000"].id
    assert get_cogs_account_id(db_session) == first
    assert (
        get_cogs_account_id(db_session, _item(db_session, seed_accounts, None)) == first
    )


def test_an_expense_typed_account_on_the_item_is_not_used_for_cogs(
    db_session, seed_accounts
):
    """The cost of a sale is cost of goods, never operating expense."""
    expense = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.EXPENSE
    )
    item = _item(db_session, seed_accounts, expense.id)
    assert get_cogs_account_id(db_session, item) == seed_accounts["5000"].id


def test_a_sale_debits_the_items_sub_account_not_the_parent(
    client, db_session, seed_accounts, seed_customer
):
    sub = _sub_cogs(db_session, seed_accounts)
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, sub.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=3, unit_rate="25.00"
    )

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.SALE)
    debits = {l.account_id: l.debit for l in lines if l.debit}
    assert debits == {sub.id: Decimal("30.00")}
    assert seed_accounts["5000"].id not in {l.account_id for l in lines}


def test_a_void_credits_the_same_sub_account(
    client, db_session, seed_accounts, seed_customer
):
    sub = _sub_cogs(db_session, seed_accounts)
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, sub.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    assert credits == {sub.id: Decimal("40.00")}


def _item_net_by_account(db, item):
    """Net debit−credit per account over every cost entry the item's
    movements posted (sale and reversal alike)."""
    txn_ids = [
        mv.transaction_id
        for mv in db.query(InventoryMovement).filter_by(item_id=item.id).all()
        if mv.transaction_id is not None
    ]
    net = {}
    for ln in (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id.in_(txn_ids))
        .all()
    ):
        net[ln.account_id] = (
            net.get(ln.account_id, Decimal("0"))
            + Decimal(str(ln.debit or 0))
            - Decimal(str(ln.credit or 0))
        )
    return net


def _asset_account(db, number="1390", name="Inventory — Widgets"):
    acc = Account(
        account_number=number,
        name=name,
        account_type=AccountType.ASSET,
        balance=Decimal("0"),
    )
    db.add(acc)
    db.commit()
    return acc


def test_a_void_reverses_onto_the_accounts_the_sale_used(
    client, db_session, seed_accounts, seed_customer
):
    """Sale to 5000, item moved to 5100, invoice voided: the reversal credits
    5000 (what the sale debited), not 5100. Otherwise 5000 stays overstated
    and 5100 goes negative. The asset side is held the same way."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)  # resolves to 5000
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )

    sub = seed_accounts["5100"]  # Materials Cost, COGS-typed
    other_asset = _asset_account(db_session)
    item = db_session.get(Item, item.id)
    item.expense_account_id = sub.id
    item.asset_account_id = other_asset.id
    db_session.commit()

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    debits = {l.account_id: l.debit for l in lines if l.debit}
    assert credits == {seed_accounts["5000"].id: Decimal("40.00")}
    assert debits == {seed_accounts["1300"].id: Decimal("40.00")}

    net = _item_net_by_account(db_session, item)
    assert net.get(seed_accounts["5000"].id, Decimal("0")) == Decimal("0")
    assert net.get(seed_accounts["1300"].id, Decimal("0")) == Decimal("0")
    assert sub.id not in net and other_asset.id not in net


def test_an_edit_that_lowers_quantity_reverses_onto_the_sales_account(
    client, db_session, seed_accounts, seed_customer
):
    """An invoice edit that drops the quantity goes through reverse_sale too:
    the units returned come off the account the sale debited."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)  # resolves to 5000
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=5, unit_rate="25.00"
    )

    sub = seed_accounts["5100"]  # Materials Cost, COGS-typed
    item = db_session.get(Item, item.id)
    item.expense_account_id = sub.id
    db_session.commit()

    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": item.id,
                    "description": "Sold less",
                    "quantity": "2",
                    "rate": "25.00",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    assert credits == {seed_accounts["5000"].id: Decimal("30.00")}

    net = _item_net_by_account(db_session, item)
    # Two units at $10 are still sold, and they stay on 5000.
    assert net.get(seed_accounts["5000"].id) == Decimal("20.00")
    assert net.get(seed_accounts["1300"].id) == Decimal("-20.00")
    assert sub.id not in net
