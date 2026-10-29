"""A credit memo that takes stock back puts its cost back too (#641, from
Discussion #633).

The return added the quantity at the item's average cost and posted no
entry, so Inventory and Cost of Goods Sold kept the sale's cost after the
goods were back on the shelf. Now the return posts DR Inventory / CR COGS
for what came back, one entry per memo, linked from the return movement:
at the original sale's cost and onto the sale's accounts when the memo
names the invoice, else at the item's average cost onto the item's
accounts. Voiding the memo reverses both, and Sales by Item nets the
return as the P&L does."""

from decimal import Decimal

from app.models.items import InventoryMovement, Item, ItemType, MovementType
from app.models.transactions import Transaction
from app.services.accounting import _q

from tests.test_inventory import (
    _create_bill_for_inventory,
    _create_invoice_for_inventory,
    _seed_vendor,
)
from tests.test_item_cogs_account import (
    _asset_account,
    _balances,
    _item,
    _repoint,
    _set_qty,
    _sub_cogs,
)

PERIOD = "start_date=2026-01-01&end_date=2026-12-31"


def _credit_memo(client, customer_id, lines, invoice_id=None, date="2026-04-20"):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": customer_id,
            "date": date,
            "tax_rate": "0",
            "original_invoice_id": invoice_id,
            "lines": [
                {
                    "item_id": item_id,
                    "description": "Returned",
                    "quantity": str(qty),
                    "rate": "25.00",
                }
                for item_id, qty in lines
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _returns(db, memo_id):
    return (
        db.query(InventoryMovement)
        .filter_by(
            source_type="credit_memo",
            source_id=memo_id,
            movement_type=MovementType.RETURN_IN,
        )
        .order_by(InventoryMovement.id)
        .all()
    )


def _entries(db, source_type, source_id):
    return (
        db.query(Transaction)
        .filter_by(source_type=source_type, source_id=source_id)
        .order_by(Transaction.id)
        .all()
    )


def _sides(txn):
    """Debits and credits per account, summed: the return entry carries a
    line per item, and two items can share an account."""
    debits: dict = {}
    credits: dict = {}
    for ln in txn.lines:
        if ln.debit:
            debits[ln.account_id] = debits.get(ln.account_id, Decimal("0")) + ln.debit
        if ln.credit:
            credits[ln.account_id] = (
                credits.get(ln.account_id, Decimal("0")) + ln.credit
            )
    return debits, credits


def _on_hand(db, item_id):
    return Decimal(str(db.get(Item, item_id).quantity_on_hand))


def _sold_three_at_ten(client, db, accounts, customer, cogs_account_id=None):
    """Stock 10 at $10, sell 3 at $25: the sale posts COGS 30 at cost 10."""
    vendor = _seed_vendor(db)
    item = _item(db, accounts, cogs_account_id)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")
    inv = _create_invoice_for_inventory(
        client, customer.id, item.id, qty=3, unit_rate="25.00"
    )
    return vendor, item, inv


def test_a_return_against_the_invoice_posts_the_sales_cost_back(
    client, db_session, seed_accounts, seed_customer
):
    """Sell 3 at cost 10 on the item's COGS sub-account; the cost then moves
    to 15 and the item to other accounts; a memo naming the invoice returns
    1: DR Inventory 10 / CR the sale's COGS account 10, not the new cost and
    not the new accounts, and the return movement links the entry and names
    the sale posting."""
    sub = _sub_cogs(db_session, seed_accounts)
    vendor, item, inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer, sub.id
    )
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=7, unit_cost="20.00", bill_number="B-2"
    )
    later_cogs = _sub_cogs(db_session, seed_accounts, number="5160", name="Later")
    later_asset = _asset_account(db_session)
    _repoint(db_session, item.id, later_cogs.id)
    it = db_session.get(Item, item.id)
    it.asset_account_id = later_asset.id
    db_session.commit()
    assert Decimal(str(it.avg_cost)) == Decimal("15.0000")  # (7 x 10 + 7 x 20) / 14

    memo = _credit_memo(client, seed_customer.id, [(item.id, 1)], invoice_id=inv["id"])

    db_session.expire_all()
    _own, returned = _entries(db_session, "credit_memo", memo["id"])
    debits, credits = _sides(returned)
    assert debits == {seed_accounts["1300"].id: Decimal("10.00")}
    assert credits == {sub.id: Decimal("10.00")}
    (mv,) = _returns(db_session, memo["id"])
    assert mv.transaction_id == returned.id
    assert Decimal(str(mv.quantity)) == Decimal("1")
    assert Decimal(str(mv.unit_cost)) == Decimal("10.0000")
    sale = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=MovementType.SALE)
        .one()
    )
    assert mv.reverses_movement_id == sale.id
    assert _on_hand(db_session, item.id) == Decimal("15")
    assert _balances(db_session, sub, later_cogs, later_asset) == {
        "5150": Decimal("20.00"),
        "5160": Decimal("0.00"),
        "1390": Decimal("0.00"),
    }


def test_a_return_naming_no_invoice_uses_the_average_cost_and_the_items_accounts(
    client, db_session, seed_accounts, seed_customer
):
    """No invoice named: the cost is the item's average at the time, onto
    the item's own COGS account, and the movement names no sale."""
    sub = _sub_cogs(db_session, seed_accounts)
    vendor, item, _inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer, sub.id
    )
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=10, unit_cost="20.00", bill_number="B-2"
    )
    # 7 at 10 and 10 at 20: 270 / 17 = 15.8824

    memo = _credit_memo(client, seed_customer.id, [(item.id, 1)])

    db_session.expire_all()
    _own, returned = _entries(db_session, "credit_memo", memo["id"])
    debits, credits = _sides(returned)
    assert debits == {seed_accounts["1300"].id: Decimal("15.88")}
    assert credits == {sub.id: Decimal("15.88")}
    (mv,) = _returns(db_session, memo["id"])
    assert Decimal(str(mv.unit_cost)) == Decimal("15.8824")
    assert mv.reverses_movement_id is None
    assert mv.transaction_id == returned.id
    assert _on_hand(db_session, item.id) == Decimal("18")


def test_voiding_the_memo_reverses_the_entry_and_the_movement(
    client, db_session, seed_accounts, seed_customer
):
    vendor, item, inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer
    )
    memo = _credit_memo(client, seed_customer.id, [(item.id, 1)], invoice_id=inv["id"])
    db_session.expire_all()
    assert _balances(db_session, seed_accounts["5000"], seed_accounts["1300"]) == {
        "5000": Decimal("20.00"),
        "1300": Decimal("80.00"),
    }

    assert client.post(f"/api/credit-memos/{memo['id']}/void").status_code == 200

    db_session.expire_all()
    # the memo's own reversal and the return's
    assert len(_entries(db_session, "credit_memo_void", memo["id"])) == 2
    (mv,) = _returns(db_session, memo["id"])
    out = (
        db_session.query(InventoryMovement)
        .filter_by(source_type="credit_memo_void", source_id=memo["id"])
        .one()
    )
    assert out.movement_type == MovementType.VOID
    assert Decimal(str(out.quantity)) == Decimal("-1")
    assert Decimal(str(out.unit_cost)) == Decimal("10.0000")
    assert out.reverses_movement_id == mv.id
    reversal = db_session.get(Transaction, out.transaction_id)
    assert reversal.source_type == "credit_memo_void"
    assert reversal.source_id == memo["id"]
    debits, credits = _sides(reversal)
    assert debits == {seed_accounts["5000"].id: Decimal("10.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("10.00")}
    assert _balances(db_session, seed_accounts["5000"], seed_accounts["1300"]) == {
        "5000": Decimal("30.00"),
        "1300": Decimal("70.00"),
    }
    assert _on_hand(db_session, item.id) == Decimal("7")
    assert client.get(f"/api/credit-memos/{memo['id']}").json()["status"] == "void"


def test_sales_by_item_ties_to_the_profit_and_loss_after_a_return(
    client, db_session, seed_accounts, seed_customer
):
    _vendor, item, inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer
    )
    _credit_memo(client, seed_customer.id, [(item.id, 1)], invoice_id=inv["id"])

    sbi = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    row = next(r for r in sbi["items"] if r["item_id"] == item.id)
    assert row["quantity"] == 2.0
    assert row["amount"] == 50.0
    assert row["cogs"] == 20.0  # 3 sold at 10, 1 back at 10
    assert row["gross_margin"] == 30.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert sbi["total_cogs"] == pl["total_cogs"] == 20.0
    assert sbi["total_amount"] == pl["total_income"] == 50.0


def test_the_movement_ledger_and_the_entry_agree_to_the_cent(
    client, db_session, seed_accounts, seed_customer
):
    """Two sale postings at different costs (the second after a dearer
    purchase) and a memo naming the invoice returns across both: one return
    movement per posting at its own cost, their amounts rounded per
    movement add up to the one entry, and Sales by Item still ties to the
    P&L."""
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
    _set_qty(client, inv["id"], item.id, 117)  # 115 more at the new average

    memo = _credit_memo(
        client, seed_customer.id, [(item.id, 116)], invoice_id=inv["id"]
    )

    db_session.expire_all()
    returns = _returns(db_session, memo["id"])
    assert [(Decimal(str(m.quantity)), Decimal(str(m.unit_cost))) for m in returns] == [
        (Decimal("115"), Decimal("10.1030")),
        (Decimal("1"), Decimal("10.0000")),
    ]
    assert all(m.reverses_movement_id for m in returns)
    _own, entry = _entries(db_session, "credit_memo", memo["id"])
    assert {m.transaction_id for m in returns} == {entry.id}
    posted = sum((ln.debit for ln in entry.lines), Decimal("0"))
    ledger = sum(
        (_q(Decimal(str(m.quantity)) * Decimal(str(m.unit_cost))) for m in returns),
        Decimal("0"),
    )
    assert posted == ledger == Decimal("1171.85")
    sbi = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert Decimal(str(sbi["total_cogs"])) == Decimal(str(pl["total_cogs"]))
    assert Decimal(str(pl["total_cogs"])) == Decimal("10.00")


def test_one_entry_per_memo_covers_every_returned_item(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    widget = _item(db_session, seed_accounts, None)
    gadget = Item(
        name="Gadget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("40.00"),
        income_account_id=seed_accounts["4000"].id,
        asset_account_id=seed_accounts["1300"].id,
        track_inventory=True,
        is_taxable=False,
    )
    db_session.add(gadget)
    db_session.commit()
    _create_bill_for_inventory(
        client, vendor.id, widget.id, qty=10, unit_cost="10.00", bill_number="B-1"
    )
    _create_bill_for_inventory(
        client, vendor.id, gadget.id, qty=10, unit_cost="4.00", bill_number="B-2"
    )

    memo = _credit_memo(client, seed_customer.id, [(widget.id, 2), (gadget.id, 3)])

    db_session.expire_all()
    entries = _entries(db_session, "credit_memo", memo["id"])
    assert len(entries) == 2  # the memo's own entry and one for both returns
    returned = entries[1]
    debits, credits = _sides(returned)
    assert debits == {seed_accounts["1300"].id: Decimal("32.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("32.00")}
    assert sorted(ln.description for ln in returned.lines) == [
        "COGS returned: Gadget",
        "COGS returned: Widget",
        "Inventory returned: Gadget",
        "Inventory returned: Widget",
    ]
    assert {m.transaction_id for m in _returns(db_session, memo["id"])} == {returned.id}


def test_returns_beyond_what_the_postings_have_left_fall_back_to_average_cost(
    client, db_session, seed_accounts, seed_customer
):
    """Sell 3 at 10, restock at 20. A memo naming the invoice returns 2, both
    off the posting at 10. A second memo returns 2 more: the posting has 1
    left at 10, the other 1 is at the average cost with no posting named.
    Void the first memo and a third memo's 1 comes off the posting at 10
    again."""
    vendor, item, inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer
    )
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=7, unit_cost="20.00", bill_number="B-2"
    )  # 14 on hand at 15

    first = _credit_memo(client, seed_customer.id, [(item.id, 2)], invoice_id=inv["id"])
    second = _credit_memo(
        client, seed_customer.id, [(item.id, 2)], invoice_id=inv["id"]
    )

    db_session.expire_all()
    sale = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=MovementType.SALE)
        .one()
    )

    def pieces(memo):
        return [
            (
                Decimal(str(m.quantity)),
                Decimal(str(m.unit_cost)),
                m.reverses_movement_id,
            )
            for m in _returns(db_session, memo["id"])
        ]

    assert pieces(first) == [(Decimal("2"), Decimal("10.0000"), sale.id)]
    # after the first return: 16 on hand worth 14 x 15 + 2 x 10 = 230
    assert pieces(second) == [
        (Decimal("1"), Decimal("10.0000"), sale.id),
        (Decimal("1"), Decimal("14.3750"), None),
    ]
    _own, entry = _entries(db_session, "credit_memo", second["id"])
    debits, _credits = _sides(entry)
    assert debits == {seed_accounts["1300"].id: Decimal("24.38")}

    assert client.post(f"/api/credit-memos/{first['id']}/void").status_code == 200
    third = _credit_memo(client, seed_customer.id, [(item.id, 1)], invoice_id=inv["id"])
    db_session.expire_all()
    assert pieces(third) == [(Decimal("1"), Decimal("10.0000"), sale.id)]


def test_a_sale_voided_after_a_return_still_comes_off_its_posting_in_full(
    client, db_session, seed_accounts, seed_customer
):
    """The void reverses the sale's 3 at its cost whatever the memo took
    back, so once the memo is voided too the accounts and the shelf are
    back where they started."""
    _vendor, item, inv = _sold_three_at_ten(
        client, db_session, seed_accounts, seed_customer
    )
    memo = _credit_memo(client, seed_customer.id, [(item.id, 1)], invoice_id=inv["id"])

    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    void = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, source_type="invoice_void")
        .one()
    )
    assert Decimal(str(void.quantity)) == Decimal("3")
    assert Decimal(str(void.unit_cost)) == Decimal("10.0000")

    assert client.post(f"/api/credit-memos/{memo['id']}/void").status_code == 200
    db_session.expire_all()
    assert _balances(db_session, seed_accounts["5000"], seed_accounts["1300"]) == {
        "5000": Decimal("0.00"),
        "1300": Decimal("100.00"),
    }
    assert _on_hand(db_session, item.id) == Decimal("10")


def test_a_memo_with_nothing_tracked_posts_no_return_entry(
    client, db_session, seed_accounts, seed_customer
):
    service = Item(
        name="Setup",
        item_type=ItemType.SERVICE,
        rate=Decimal("50.00"),
        income_account_id=seed_accounts["4000"].id,
        is_taxable=False,
    )
    db_session.add(service)
    db_session.commit()

    memo = _credit_memo(client, seed_customer.id, [(service.id, 1)])

    db_session.expire_all()
    assert len(_entries(db_session, "credit_memo", memo["id"])) == 1
    assert _returns(db_session, memo["id"]) == []
