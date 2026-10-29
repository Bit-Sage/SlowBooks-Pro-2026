"""Voiding or editing a bill takes its stock back out at the cost it came
in at (#646, from the 2.31.0 gate), and what no average can carry goes to
cost of goods (#647).

A bill's receipt is costed from what the bill put on Inventory: the line's
rate with its share of the tax. The void and the edit reversed it at the
line's rate, and an edit added an item's lines together at the last line's
rate, so after either the ledger's Inventory no longer equalled quantity x
average cost, and selling out left a figure in Inventory with nothing on
hand. Each receipt now comes back out by itself at the cost it carried, one
reversal movement naming it, as a sale's void walks its postings."""

from decimal import Decimal

from app.models.accounts import AccountType
from app.models.items import InventoryMovement, Item, MovementType
from app.services.accounting import _q

from tests.test_credit_memo_return_cogs import _entries, _on_hand, _sides
from tests.test_inventory import _create_invoice_for_inventory, _seed_vendor
from tests.test_item_cogs_account import _balances, _item


def _stock(item_id, qty, rate):
    return {
        "item_id": item_id,
        "description": "Stock receipt",
        "quantity": str(qty),
        "rate": str(rate),
    }


def _bill(client, vendor_id, lines, tax_rate="0", bill_number="B-1"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": bill_number,
            "date": "2026-04-01",
            "terms": "Net 30",
            "tax_rate": tax_rate,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _edit_lines(client, bill_id, lines):
    r = client.put(f"/api/bills/{bill_id}", json={"lines": lines})
    assert r.status_code == 200, r.text
    return r.json()


def _movements(db, item_id, movement_type):
    return (
        db.query(InventoryMovement)
        .filter_by(item_id=item_id, movement_type=movement_type)
        .order_by(InventoryMovement.id)
        .all()
    )


def _void_shape(mv):
    return (
        Decimal(str(mv.quantity)),
        Decimal(str(mv.unit_cost)),
        mv.reverses_movement_id,
        mv.source_type,
    )


def _inventory_is_the_stock(db, accounts, item_id):
    """The ledger's Inventory equals quantity x average cost to the cent;
    returns the figure."""
    it = db.get(Item, item_id)
    ledger = _balances(db, accounts["1300"])["1300"]
    assert ledger == _q(Decimal(str(it.quantity_on_hand)) * Decimal(str(it.avg_cost)))
    return ledger


def _average(db, item_id):
    return Decimal(str(db.get(Item, item_id).avg_cost))


def _sell_out(client, db, accounts, customer, item_id):
    """Sell what is on hand: nothing stays in Inventory or on the shelf."""
    _create_invoice_for_inventory(
        client, customer.id, item_id, qty=_on_hand(db, item_id), unit_rate="25.00"
    )
    db.expire_all()
    assert _on_hand(db, item_id) == Decimal("0")
    assert _inventory_is_the_stock(db, accounts, item_id) == Decimal("0.00")
    assert client.get("/api/items/valuation").json()["total_value"] == 0.0


def test_a_taxed_bills_void_takes_the_stock_out_at_the_cost_it_came_in_at(
    client, db_session, seed_accounts, seed_customer
):
    """10 at 10.00 taxed 10% is received at 11.00, the 110.00 the bill put on
    Inventory; 10 more at 14.00 make the average 12.50. The void takes the
    first ten out at 11.00, not the line's 10.00, naming the receipt and the
    reversing entry: Inventory is 140.00 = 10 x 14.00, and selling the ten
    leaves 0.00 with nothing on hand."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    taxed = _bill(client, vendor.id, [_stock(item.id, 10, "10.00")], tax_rate="0.10")
    _bill(client, vendor.id, [_stock(item.id, 10, "14.00")], bill_number="B-2")
    db_session.expire_all()
    receipt, _other = _movements(db_session, item.id, MovementType.PURCHASE)
    assert Decimal(str(receipt.unit_cost)) == Decimal("11.0000")
    assert _average(db_session, item.id) == Decimal("12.5000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "250.00"
    )

    assert client.post(f"/api/bills/{taxed['id']}/void").status_code == 200

    db_session.expire_all()
    (void,) = _movements(db_session, item.id, MovementType.VOID)
    (reversal,) = _entries(db_session, "bill_void", taxed["id"])
    assert _void_shape(void) == (
        Decimal("-10"),
        Decimal("11.0000"),
        receipt.id,
        "bill_void",
    )
    assert void.transaction_id == reversal.id
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "140.00"
    )
    assert client.get("/api/items/valuation").json()["total_value"] == 140.0

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)
    assert _balances(db_session, seed_accounts["5000"])["5000"] == Decimal("140.00")


def test_an_edit_reverses_each_line_of_an_item_at_the_cost_it_came_in_at(
    client, db_session, seed_accounts, seed_customer
):
    """Ten already in stock at 10.00. A bill puts the same item on two lines,
    10 at 5.00 and 10 at 10.00, and is then edited to a line that stocks
    nothing: each receipt comes out at its own cost, 50.00 and 100.00, what
    the bill posted, not twenty at the last line's 10.00 (which left 50.00
    in Inventory with nothing on hand). Inventory stays 100.00 = 10 x 10.00
    and selling the ten leaves 0.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    two = _bill(
        client,
        vendor.id,
        [_stock(item.id, 10, "5.00"), _stock(item.id, 10, "10.00")],
        bill_number="B-2",
    )
    db_session.expire_all()
    _first, cheap, dear = _movements(db_session, item.id, MovementType.PURCHASE)
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "250.00"
    )
    freight = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.EXPENSE
    )

    _edit_lines(
        client,
        two["id"],
        [
            {
                "account_id": freight.id,
                "description": "Freight",
                "quantity": "1",
                "rate": "150.00",
            }
        ],
    )

    db_session.expire_all()
    voids = _movements(db_session, item.id, MovementType.VOID)
    assert [_void_shape(v) for v in voids] == [
        (Decimal("-10"), Decimal("5.0000"), cheap.id, "bill_edit"),
        (Decimal("-10"), Decimal("10.0000"), dear.id, "bill_edit"),
    ]
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "100.00"
    )
    assert _entries(db_session, "bill_edit", two["id"]) == []  # nothing left over

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)


def test_a_void_after_an_edit_reverses_the_receipt_the_edit_left_at_its_booked_cost(
    client, db_session, seed_accounts, seed_customer
):
    """10 at 10.00 taxed 10% (received at 11.00) is edited to 10 at 12.00,
    still taxed (received again at 13.20); another bill brings 10 at 14.00
    and the average to 13.60, and the item's own cost is changed to 20.00.
    The void reverses the one receipt the edit left standing, at the 13.20
    it was booked at: not the first receipt (the edit already took it out),
    not the line's 12.00, not the average, not the item's cost. Inventory is
    140.00 = 10 x 14.00 and selling out leaves 0.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    bill = _bill(client, vendor.id, [_stock(item.id, 10, "10.00")], tax_rate="0.10")
    _edit_lines(client, bill["id"], [_stock(item.id, 10, "12.00")])
    _bill(client, vendor.id, [_stock(item.id, 10, "14.00")], bill_number="B-2")
    it = db_session.get(Item, item.id)
    it.cost = Decimal("20.00")
    db_session.commit()
    db_session.expire_all()
    first, again, _other = _movements(db_session, item.id, MovementType.PURCHASE)
    assert [Decimal(str(m.unit_cost)) for m in (first, again)] == [
        Decimal("11.0000"),
        Decimal("13.2000"),
    ]
    (edit_void,) = _movements(db_session, item.id, MovementType.VOID)
    assert edit_void.reverses_movement_id == first.id
    assert _average(db_session, item.id) == Decimal("13.6000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "272.00"
    )

    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200

    db_session.expire_all()
    _edit_void, void = _movements(db_session, item.id, MovementType.VOID)
    assert _void_shape(void) == (
        Decimal("-10"),
        Decimal("13.2000"),
        again.id,
        "bill_void",
    )
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "140.00"
    )

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)


def test_a_bill_edited_on_an_earlier_release_and_voided_now_reverses_what_the_edit_left(
    client, db_session, seed_accounts
):
    """An upgraded install: the edit's reversal from before the link existed
    names no receipt, so it is charged to the oldest receipt, and the void
    takes out only the receipt the edit left standing, at its cost."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    bill = _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    _edit_lines(client, bill["id"], [_stock(item.id, 10, "12.00")])
    for v in _movements(db_session, item.id, MovementType.VOID):
        v.reverses_movement_id = None
    db_session.commit()
    _bill(client, vendor.id, [_stock(item.id, 10, "14.00")], bill_number="B-2")
    db_session.expire_all()
    _first, again, _other = _movements(db_session, item.id, MovementType.PURCHASE)
    assert _average(db_session, item.id) == Decimal("13.0000")

    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200

    db_session.expire_all()
    _legacy, void = _movements(db_session, item.id, MovementType.VOID)
    assert _void_shape(void) == (
        Decimal("-10"),
        Decimal("12.0000"),
        again.id,
        "bill_void",
    )
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "140.00"
    )


def test_a_void_worth_more_than_the_stock_holds_posts_the_difference_to_cost_of_goods(
    client, db_session, seed_accounts, seed_customer
):
    """10 at 10.00 and 10 at 14.00 (average 12.00); 18 sold at 12.00; the
    dearer bill voided with 2 on hand. The void takes 140.00 out of a pool
    holding 24.00: the count goes to -8 at the kept average, and the 20.00
    the pool could not give is posted Dr Inventory / Cr Cost of Goods Sold
    under the void (#647), so Inventory reads -96.00 = -8 x 12.00 and cost
    of goods 196.00: the ten at 10.00 and the eight owed at 12.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    dear = _bill(client, vendor.id, [_stock(item.id, 10, "14.00")], bill_number="B-2")
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=18, unit_rate="25.00"
    )
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "24.00"
    )

    assert client.post(f"/api/bills/{dear['id']}/void").status_code == 200

    db_session.expire_all()
    reversal, residue = _entries(db_session, "bill_void", dear["id"])
    assert residue.description == (
        "VOID Bill B-2 - Inventory set to quantity x average cost: Widget"
    )
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("20.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("20.00")}
    (void,) = [
        v
        for v in _movements(db_session, item.id, MovementType.VOID)
        if v.source_type == "bill_void"
    ]
    assert void.transaction_id == reversal.id
    assert _on_hand(db_session, item.id) == Decimal("-8")
    assert _average(db_session, item.id) == Decimal("12.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-96.00"
    )
    assert _balances(db_session, seed_accounts["5000"])["5000"] == Decimal("196.00")
