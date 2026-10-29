"""Stock coming in while the count is below zero fills the hole first (W-60,
from the 2.31.1 gate: the receiving side of #647).

Units sold ahead of their receipt were costed at the average of the time.
A receipt that lands while the count is below zero first fills that hole,
and the difference between what those units were costed at and what they
cost now goes to the item's cost of goods account (Dr Cost of Goods Sold /
Cr Inventory when the receipt costs more, the reverse when less), posted
under the document that brought the stock in. What is left of the receipt
past the hole is stock at the receipt's own price, the new average. Before,
a receipt into a count below zero kept or zeroed the average and posted
nothing, so the ledger's Inventory and quantity x average cost parted: bill
5 at 10.00, invoice 8, bill 3 at 14.00 left 0 on hand and 12.00 in Inventory.
The same rule covers every way stock comes in: a sale's void, a credit
memo's return and a count adjustment up fill the hole the same way."""

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.items import MovementType

from tests.test_bill_reversal_cost import (
    _average,
    _bill,
    _edit_lines,
    _inventory_is_the_stock,
    _movements,
    _sell_out,
    _stock,
)
from tests.test_credit_memo_return_cogs import (
    _credit_memo,
    _entries,
    _on_hand,
    _residue_entry,
    _sides,
)
from tests.test_inventory import _create_invoice_for_inventory, _seed_vendor
from tests.test_item_cogs_account import _balances, _item


def _three_sold_ahead(client, db, accounts, customer):
    """Bill B-1 brings 5 at 10.00 and an invoice sells 8: three are sold
    ahead at 10.00, Inventory -30.00, cost of goods 80.00."""
    vendor = _seed_vendor(db)
    item = _item(db, accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 5, "10.00")])
    invoice = _create_invoice_for_inventory(
        client, customer.id, item.id, qty=8, unit_rate="25.00"
    )
    db.expire_all()
    assert _on_hand(db, item.id) == Decimal("-3")
    assert _average(db, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db, accounts, item.id) == Decimal("-30.00")
    assert _balances(db, accounts["5000"])["5000"] == Decimal("80.00")
    return vendor, item, invoice


def _cogs(db, accounts):
    return _balances(db, accounts["5000"])["5000"]


def test_a_receipt_that_fills_the_hole_exactly_leaves_nothing_in_inventory(
    client, db_session, seed_accounts, seed_customer
):
    """Bill B-2 brings the 3 that were sold ahead, at 14.00: each costs 4.00
    more than it was costed at, and that 12.00 goes to cost of goods under
    the bill, so Inventory reads 0.00 with nothing on hand (it read 12.00)
    and cost of goods the 92.00 the eight cost."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    second = _bill(client, vendor.id, [_stock(item.id, 3, "14.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _average(db_session, item.id) == Decimal("0.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    residue = _residue_entry(db_session, "bill", second["id"])
    assert residue.description == (
        "Bill B-2 line 1 - Inventory set to quantity x average cost: Widget"
    )
    assert str(residue.date) == "2026-04-01"  # the bill's date
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["5000"].id: Decimal("12.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("12.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("92.00")
    assert client.get("/api/items/valuation").json()["total_value"] == 0.0


def test_a_receipt_smaller_than_the_hole_is_costed_against_the_units_sold_ahead(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00, bill B-2 brings 1 at 14.00: it fills one unit of the
    hole, the 4.00 it cost beyond what that unit was costed at goes to cost
    of goods, and the count is -2 at the kept average of 10.00, Inventory
    -20.00 (it read -16.00)."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    second = _bill(client, vendor.id, [_stock(item.id, 1, "14.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-2")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-20.00"
    )
    debits, credits = _sides(_residue_entry(db_session, "bill", second["id"]))
    assert debits == {seed_accounts["5000"].id: Decimal("4.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("4.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("84.00")


def test_a_receipt_larger_than_the_hole_costs_the_rest_at_its_own_price(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00, bill B-2 brings 5 at 14.00: three fill the hole
    (12.00 to cost of goods) and the two left are stock at 14.00, the new
    average, so Inventory reads 28.00 = 2 x 14.00. Selling those two leaves
    0.00, and cost of goods is then the 120.00 both bills cost."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    second = _bill(client, vendor.id, [_stock(item.id, 5, "14.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _average(db_session, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "28.00"
    )
    debits, credits = _sides(_residue_entry(db_session, "bill", second["id"]))
    assert debits == {seed_accounts["5000"].id: Decimal("12.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("12.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("92.00")

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)
    assert _cogs(db_session, seed_accounts) == Decimal("120.00")


def test_a_receipt_cheaper_than_the_units_sold_ahead_gives_cost_of_goods_back(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00, bill B-2 brings 3 at 8.00: the three were costed at
    2.00 more each than they turned out to cost, so 6.00 comes back out of
    cost of goods (Dr Inventory / Cr Cost of Goods Sold), Inventory reads
    0.00 with nothing on hand, and cost of goods is the 74.00 the eight
    cost."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    second = _bill(client, vendor.id, [_stock(item.id, 3, "8.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    debits, credits = _sides(_residue_entry(db_session, "bill", second["id"]))
    assert debits == {seed_accounts["1300"].id: Decimal("6.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("6.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("74.00")


def test_a_receipt_into_stock_on_hand_blends_as_before_and_posts_nothing_more(
    client, db_session, seed_accounts
):
    """Nothing sold ahead, nothing to fill: 5 at 10.00 and 5 at 14.00 average
    12.00, and neither bill posts an entry beyond its own."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    first = _bill(client, vendor.id, [_stock(item.id, 5, "10.00")])
    second = _bill(client, vendor.id, [_stock(item.id, 5, "14.00")], bill_number="B-2")
    db_session.expire_all()
    assert _average(db_session, item.id) == Decimal("12.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "120.00"
    )
    assert len(_entries(db_session, "bill", first["id"])) == 1
    assert len(_entries(db_session, "bill", second["id"])) == 1


def test_an_edit_below_what_has_sold_ends_with_inventory_at_quantity_times_average(
    client, db_session, seed_accounts, seed_customer
):
    """The #646 edit path: bill B-1 brings 10 at 10.00 and 8 sell, 2 on hand.
    The bill is edited to 5 at 12.00: the ten come back out at 10.00 (the
    count goes to -8 at that average, nothing left over) and the five are
    received into the hole at 12.00, 2.00 more each than the units sold
    ahead were costed at, so 10.00 goes to cost of goods under the bill.
    The count is -3 at 10.00 and Inventory reads -30.00; cost of goods is
    90.00, five at 12.00 and three still owed at 10.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    bill = _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=8, unit_rate="25.00"
    )
    db_session.expire_all()
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "20.00"
    )

    _edit_lines(client, bill["id"], [_stock(item.id, 5, "12.00")])

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-3")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-30.00"
    )
    assert _entries(db_session, "bill_edit", bill["id"]) == []  # nothing left over
    residue = _residue_entry(db_session, "bill", bill["id"])
    assert residue.description == (
        "Bill B-1 line 1 - Inventory set to quantity x average cost: Widget"
    )
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["5000"].id: Decimal("10.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("10.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("90.00")
    assert client.get("/api/items/valuation").json()["total_value"] == -30.0


def test_a_void_after_the_stock_sold_and_the_restock_end_at_quantity_times_average(
    client, db_session, seed_accounts, seed_customer
):
    """The #646 void path: bill B-1 brings 5 at 10.00 and all five sell. The
    void takes the five out of an empty pool (#647 gives the 50.00 back to
    cost of goods: -5 on hand at an average of zero, Inventory 0.00). Bill
    B-2 then brings 5 at 13.00 into the hole: the five sold were costed at
    nothing, so all 65.00 goes to cost of goods, and Inventory reads 0.00
    with nothing on hand (it read 65.00 against 0.00)."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    first = _bill(client, vendor.id, [_stock(item.id, 5, "10.00")])
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=5, unit_rate="25.00"
    )
    assert client.post(f"/api/bills/{first['id']}/void").status_code == 200
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-5")
    assert _average(db_session, item.id) == Decimal("0.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("0.00")

    second = _bill(client, vendor.id, [_stock(item.id, 5, "13.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    debits, credits = _sides(_residue_entry(db_session, "bill", second["id"]))
    assert debits == {seed_accounts["5000"].id: Decimal("65.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("65.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("65.00")
    assert client.get("/api/items/valuation").json()["total_value"] == 0.0


# -------------------------------------------------------------------------
# The other ways stock comes in fill the hole the same way
# -------------------------------------------------------------------------


def _two_sold_ahead_at_fourteen(client, db, accounts, customer):
    """Three sold ahead at 10.00 (invoice A), then bill B-2 brings 5 at 14.00
    (the hole filled, two on hand at 14.00) and invoice B sells 4: -2 on
    hand at 14.00, Inventory -28.00, cost of goods 148.00."""
    vendor, item, invoice_a = _three_sold_ahead(client, db, accounts, customer)
    _bill(client, vendor.id, [_stock(item.id, 5, "14.00")], bill_number="B-2")
    _create_invoice_for_inventory(client, customer.id, item.id, qty=4, unit_rate="25")
    db.expire_all()
    assert _on_hand(db, item.id) == Decimal("-2")
    assert _average(db, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db, accounts, item.id) == Decimal("-28.00")
    assert _cogs(db, accounts) == Decimal("148.00")
    return item, invoice_a


def test_a_sales_void_into_a_count_below_zero_fills_the_hole_at_the_sales_cost(
    client, db_session, seed_accounts, seed_customer
):
    """Voiding invoice A brings its 8 back at the 10.00 they were sold at:
    two fill the hole left by units costed at 14.00, so 8.00 comes back out
    of cost of goods under the void, and the six on hand are at 10.00,
    Inventory 60.00 (the old blend made the average 8.6667). Cost of goods
    is 60.00: both bills cost 120.00 and 60.00 of it is on the shelf."""
    item, invoice_a = _two_sold_ahead_at_fourteen(
        client, db_session, seed_accounts, seed_customer
    )

    assert client.post(f"/api/invoices/{invoice_a['id']}/void").status_code == 200

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("6")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "60.00"
    )
    residue = _residue_entry(db_session, "invoice_void", invoice_a["id"])
    assert residue.description == (
        "Sale reversal - Inventory set to quantity x average cost: Widget"
    )
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("8.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("8.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("60.00")


def test_a_return_into_a_count_below_zero_fills_the_hole_at_the_returns_cost(
    client, db_session, seed_accounts, seed_customer
):
    """A credit memo against invoice A returns 1 at the 10.00 it was sold at,
    into a hole of units costed at 14.00: 4.00 comes back out of cost of
    goods under the memo, the count is -1 at the kept 14.00 and Inventory
    reads -14.00 (it read -18.00). Cost of goods is 134.00: 120.00 of stock
    bought and 14.00 still owed."""
    item, invoice_a = _two_sold_ahead_at_fourteen(
        client, db_session, seed_accounts, seed_customer
    )

    memo = _credit_memo(
        client, seed_customer.id, [(item.id, 1)], invoice_id=invoice_a["id"]
    )

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-1")
    assert _average(db_session, item.id) == Decimal("14.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-14.00"
    )
    residue = _residue_entry(db_session, "credit_memo", memo["id"])
    assert residue.description.endswith(
        " - Inventory set to quantity x average cost: Widget"
    )
    assert residue.description.startswith("Return: ")
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("4.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("4.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("134.00")


def test_a_count_adjustment_up_into_a_count_below_zero_fills_the_hole_too(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00, a count correction finds 5 at 12.00: the adjustment
    posts its 60.00 to 5900 as before, three fill the hole (6.00 to cost of
    goods under the adjustment) and the two on hand are at 12.00, Inventory
    24.00 (the old blend made the average 15.00)."""
    adjustments = Account(
        account_number="5900",
        name="Inventory Adjustments",
        account_type=AccountType.EXPENSE,
        balance=Decimal("0"),
    )
    db_session.add(adjustments)
    db_session.commit()
    _vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    r = client.post(
        f"/api/items/{item.id}/adjust",
        json={"quantity_delta": "5", "unit_cost": "12.00", "memo": "Found 5"},
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    assert r.json()["memo"] == "Found 5"
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _average(db_session, item.id) == Decimal("12.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "24.00"
    )
    residue = _residue_entry(db_session, "adjustment", item.id)
    assert residue.description == (
        "Inventory adjustment - Inventory set to quantity x average cost: Widget"
    )
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["5000"].id: Decimal("6.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("6.00")}
    assert _balances(db_session, adjustments)["5900"] == Decimal("-60.00")
    assert _cogs(db_session, seed_accounts) == Decimal("86.00")
    (adjustment,) = _movements(db_session, item.id, MovementType.ADJUSTMENT)
    assert Decimal(str(adjustment.unit_cost)) == Decimal("12.0000")
