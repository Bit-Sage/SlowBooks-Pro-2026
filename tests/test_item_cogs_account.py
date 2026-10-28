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


def test_an_inactive_cogs_account_on_the_item_is_not_reopened(
    db_session, seed_accounts
):
    """A retired account stays retired: the sale falls back instead."""
    sub = _sub_cogs(db_session, seed_accounts, number="5130", name="Retired COGS")
    sub.is_active = False
    db_session.commit()
    item = _item(db_session, seed_accounts, sub.id)
    assert get_cogs_account_id(db_session, item) == seed_accounts["5000"].id


def test_a_void_reverses_against_the_account_the_sale_posted_to(
    client, db_session, seed_accounts, seed_customer
):
    """The item's COGS account changed between the sale and the void: the
    reversal still comes off the original account, or both sub-accounts
    would carry a residual forever (found by the sb-qa skeptic)."""
    first = _sub_cogs(db_session, seed_accounts, number="5110", name="Cost of Widgets")
    second = _sub_cogs(db_session, seed_accounts, number="5120", name="Cost of Gadgets")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, first.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    item = db_session.query(Item).filter_by(id=item.id).first()
    item.expense_account_id = second.id
    db_session.commit()
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    assert credits == {first.id: Decimal("40.00")}


def test_a_quantity_down_edit_reverses_against_the_original_account(
    client, db_session, seed_accounts, seed_customer
):
    first = _sub_cogs(db_session, seed_accounts, number="5110", name="Cost of Widgets")
    second = _sub_cogs(db_session, seed_accounts, number="5120", name="Cost of Gadgets")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, first.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    item = db_session.query(Item).filter_by(id=item.id).first()
    item.expense_account_id = second.id
    db_session.commit()
    full = client.get(f"/api/invoices/{inv['id']}").json()
    line = full["lines"][0]
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "customer_id": full["customer_id"],
            "date": full["date"],
            "due_date": full["due_date"],
            "lines": [
                {
                    "item_id": item.id,
                    "description": line["description"],
                    "quantity": "1",
                    "rate": "25.00",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    assert credits == {first.id: Decimal("30.00")}


def _balances(db, *accounts):
    out = {}
    for a in accounts:
        rows = (
            db.query(TransactionLine).filter(TransactionLine.account_id == a.id).all()
        )
        out[a.account_number] = sum((l.debit - l.credit for l in rows), Decimal("0"))
    return out


def _repoint(db, item_id, account_id):
    it = db.query(Item).filter_by(id=item_id).first()
    it.expense_account_id = account_id
    db.commit()


def _set_qty(client, inv_id, item_id, qty):
    full = client.get(f"/api/invoices/{inv_id}").json()
    r = client.put(
        f"/api/invoices/{inv_id}",
        json={
            "customer_id": full["customer_id"],
            "date": full["date"],
            "due_date": full["due_date"],
            "lines": [
                {
                    "item_id": item_id,
                    "description": "Sold widget",
                    "quantity": str(qty),
                    "rate": "25.00",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text


def test_a_void_reverses_every_sale_posting_where_it_went_on(
    client, db_session, seed_accounts, seed_customer
):
    """Sale on A, item repointed to B, quantity edited up (a second posting,
    on B), then void: each posting comes off its own account and both
    sub-accounts return to zero (found by the sb-qa skeptic)."""
    a = _sub_cogs(db_session, seed_accounts, number="5110", name="A")
    b = _sub_cogs(db_session, seed_accounts, number="5120", name="B")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, a.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=20, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    _repoint(db_session, item.id, b.id)
    _set_qty(client, inv["id"], item.id, 6)
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("40.00"),
        "5120": Decimal("20.00"),
    }

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("0.00"),
        "5120": Decimal("0.00"),
    }
    lines = _cogs_lines(db_session, item, MovementType.VOID)
    credits = {l.account_id: l.credit for l in lines if l.credit}
    assert credits == {a.id: Decimal("40.00"), b.id: Decimal("20.00")}


def test_a_void_after_a_partial_reversal_takes_the_remaining_postings(
    client, db_session, seed_accounts, seed_customer
):
    """Sale 4 on A, repoint to B, up to 6 (2 on B), back down to 4 (the 2 on
    B reversed), then void: the void must come off A alone."""
    a = _sub_cogs(db_session, seed_accounts, number="5110", name="A")
    b = _sub_cogs(db_session, seed_accounts, number="5120", name="B")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, a.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=20, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    _repoint(db_session, item.id, b.id)
    _set_qty(client, inv["id"], item.id, 6)
    _set_qty(client, inv["id"], item.id, 4)
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("40.00"),
        "5120": Decimal("0.00"),
    }

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("0.00"),
        "5120": Decimal("0.00"),
    }


def test_a_void_restores_the_inventory_account_the_sale_credited(
    client, db_session, seed_accounts, seed_customer
):
    """The item's inventory asset account changed after the sale: the
    reversal still debits the account the sale credited."""
    other = Account(
        account_number="1310",
        name="Inventory — Other",
        account_type=AccountType.ASSET,
        balance=Decimal("0"),
    )
    db_session.add(other)
    db_session.commit()
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    it = db_session.query(Item).filter_by(id=item.id).first()
    it.asset_account_id = other.id
    db_session.commit()
    before = _balances(db_session, seed_accounts["1300"], other)
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    after = _balances(db_session, seed_accounts["1300"], other)
    assert after["1300"] == before["1300"] + Decimal("40.00")
    assert after["1310"] == Decimal("0.00")


def test_an_edit_down_before_a_later_edit_up_does_not_confuse_the_void(
    client, db_session, seed_accounts, seed_customer
):
    """Sale 4 on A; down to 2 (2 off A); repoint to B; up to 5 (3 on B);
    void: 3 off B and 2 off A, both back to zero (sb-qa skeptic, round 3)."""
    a = _sub_cogs(db_session, seed_accounts, number="5110", name="A")
    b = _sub_cogs(db_session, seed_accounts, number="5120", name="B")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, a.id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=30, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    _set_qty(client, inv["id"], item.id, 2)
    _repoint(db_session, item.id, b.id)
    _set_qty(client, inv["id"], item.id, 5)
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("20.00"),
        "5120": Decimal("30.00"),
    }

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    assert _balances(db_session, a, b) == {
        "5110": Decimal("0.00"),
        "5120": Decimal("0.00"),
    }
    voids = (
        db_session.query(InventoryMovement)
        .filter_by(
            item_id=item.id, movement_type=MovementType.VOID, source_type="invoice_void"
        )
        .all()
    )
    assert sorted(Decimal(str(v.quantity)) for v in voids) == [
        Decimal("2"),
        Decimal("3"),
    ]
    assert all(v.reverses_movement_id for v in voids)


def test_a_reversal_over_mixed_costs_keeps_sales_by_item_tied_to_the_pl(
    client, db_session, seed_accounts, seed_customer
):
    """Two postings at different costs reversed together: one movement per
    posting at its own cost, so the movement ledger and the entry agree to
    the cent and Sales by Item still ties to the P&L."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=200, unit_cost="10.00", bill_number="B-1"
    )
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=2, unit_rate="25.00"
    )
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=1, unit_cost="30.50", bill_number="B-2"
    )
    _set_qty(client, inv["id"], item.id, 117)
    _set_qty(client, inv["id"], item.id, 1)
    db_session.expire_all()
    movements = db_session.query(InventoryMovement).filter_by(item_id=item.id).all()
    ledger = sum(
        (
            Decimal(str(m.quantity)) * Decimal(str(m.unit_cost))
            for m in movements
            if m.movement_type in (MovementType.SALE, MovementType.VOID)
        ),
        Decimal("0"),
    )
    cogs_lines = (
        db_session.query(TransactionLine)
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(Account.account_type == AccountType.COGS)
        .all()
    )
    posted = sum((l.debit - l.credit for l in cogs_lines), Decimal("0"))
    assert posted == -ledger.quantize(Decimal("0.01"))
    sbi = client.get(
        "/api/reports/sales-by-item?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    pl = client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert Decimal(str(sbi["total_cogs"])) == Decimal(str(pl["total_cogs"]))


def test_a_pre_release_reversal_is_charged_to_the_oldest_posting(
    client, db_session, seed_accounts, seed_customer
):
    """An upgraded install: a reversal from before the link existed (null
    link) can only have consumed the postings of its day, so it comes off
    the oldest posting, never one made after the upgrade (sb-qa, round 4)."""
    b = _sub_cogs(db_session, seed_accounts, number="5120", name="B")
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)  # posts to 5000, as pre-2.30
    _create_bill_for_inventory(client, vendor.id, item.id, qty=30, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=4, unit_rate="25.00"
    )
    _set_qty(client, inv["id"], item.id, 2)
    # make that reversal look pre-release: no link
    for v in db_session.query(InventoryMovement).filter_by(
        item_id=item.id, movement_type=MovementType.VOID
    ):
        v.reverses_movement_id = None
    db_session.commit()
    _repoint(db_session, item.id, b.id)
    _set_qty(client, inv["id"], item.id, 5)
    db_session.expire_all()
    assert _balances(db_session, seed_accounts["5000"], b) == {
        "5000": Decimal("20.00"),
        "5120": Decimal("30.00"),
    }

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    assert _balances(db_session, seed_accounts["5000"], b) == {
        "5000": Decimal("0.00"),
        "5120": Decimal("0.00"),
    }
