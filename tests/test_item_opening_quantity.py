"""An item created with stock on hand posts that stock (SlowBooks-Pro-Testing
#101, found in PNWImport's fork).

Before, POST /api/items with track_inventory and quantity_on_hand 10 wrote
10 onto the item and nothing else: no movement, no journal entry. The stock
never reached the balance sheet, and a later sale posted COGS against value
that was never booked. Now it posts as QuickBooks does: one movement at the
item's cost and DR Inventory / CR Opening Balance Equity for qty x cost.
"""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account
from app.models.items import InventoryMovement, Item
from app.models.transactions import Transaction, TransactionLine


def _create(client, seed_accounts, name="Widget", qty="10", cost="4.00", **extra):
    body = {
        "name": name,
        "item_type": "product",
        "rate": "25.00",
        "cost": cost,
        "track_inventory": True,
        "quantity_on_hand": qty,
        "asset_account_id": seed_accounts["1300"].id,
        "is_taxable": False,
        **extra,
    }
    r = client.post("/api/items", json=body)
    return r


def _bs_amount(client, number):
    bs = client.get("/api/reports/balance-sheet").json()
    rows = bs["assets"] + bs["liabilities"] + bs["equity"]
    return sum(r["amount"] for r in rows if r.get("account_number") == number)


def _lines(db_session, txn_id):
    out = {}
    for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn_id):
        num = db_session.get(Account, ln.account_id).account_number
        out[num] = (Decimal(str(ln.debit)), Decimal(str(ln.credit)))
    return out


def test_tracked_item_with_quantity_posts_movement_and_opening_entry(
    client, db_session, seed_accounts
):
    before = _bs_amount(client, "1300")
    r = _create(client, seed_accounts)
    assert r.status_code == 201, r.text
    item = r.json()
    assert Decimal(item["quantity_on_hand"]) == 10
    assert Decimal(item["avg_cost"]) == Decimal("4.00")

    mvs = db_session.query(InventoryMovement).filter_by(item_id=item["id"]).all()
    assert len(mvs) == 1
    mv = mvs[0]
    assert mv.quantity == 10 and mv.unit_cost == Decimal("4.00")
    assert mv.source_type == "opening_balance" and mv.transaction_id

    txn = db_session.get(Transaction, mv.transaction_id)
    assert txn.source_type == "opening_balance" and txn.source_id == item["id"]
    assert txn.date == date.today()
    lines = _lines(db_session, txn.id)
    obe = db_session.query(Account).filter_by(name="Opening Balance Equity").one()
    assert lines["1300"] == (Decimal("40.00"), Decimal("0"))
    assert lines[obe.account_number] == (Decimal("0"), Decimal("40.00"))

    assert _bs_amount(client, "1300") == before + 40


def test_zero_cost_moves_stock_without_a_journal_entry(
    client, db_session, seed_accounts
):
    r = _create(client, seed_accounts, name="Freebie", cost="0")
    assert r.status_code == 201, r.text
    mvs = db_session.query(InventoryMovement).filter_by(item_id=r.json()["id"]).all()
    assert len(mvs) == 1 and mvs[0].quantity == 10
    assert mvs[0].transaction_id is None
    assert (
        db_session.query(Transaction).filter_by(source_type="opening_balance").count()
        == 0
    )


def test_untracked_item_keeps_its_quantity_as_given(client, db_session, seed_accounts):
    r = _create(client, seed_accounts, name="Bulk Gravel", track_inventory=False)
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["quantity_on_hand"]) == 10
    assert db_session.query(InventoryMovement).count() == 0
    assert db_session.query(Transaction).count() == 0


def test_negative_starting_quantity_is_refused(client, db_session, seed_accounts):
    r = _create(client, seed_accounts, name="Backorder", qty="-2")
    assert r.status_code == 400
    assert db_session.query(Item).filter_by(name="Backorder").count() == 0


def test_closing_date_refuses_the_opening_entry(client, db_session, seed_accounts):
    r = client.put("/api/settings", json={"closing_date": date.today().isoformat()})
    assert r.status_code == 200, r.text
    r = _create(client, seed_accounts, name="Late Widget")
    assert r.status_code in (400, 403, 409), r.text
    assert db_session.query(Item).filter_by(name="Late Widget").count() == 0


def test_update_leaves_on_hand_alone(client, db_session, seed_accounts):
    item = _create(client, seed_accounts).json()
    r = client.put(f"/api/items/{item['id']}", json={"description": "blue"})
    assert r.status_code == 200, r.text
    assert (
        db_session.query(InventoryMovement).filter_by(item_id=item["id"]).count() == 1
    )


def test_a_sale_after_opening_posts_cogs_at_opening_cost(
    client, db_session, seed_accounts, seed_customer
):
    item = _create(client, seed_accounts).json()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": date.today().isoformat(),
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": item["id"],
                    "description": "Sold",
                    "quantity": "3",
                    "rate": "25.00",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    sale = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item["id"], source_type="invoice")
        .one()
    )
    lines = _lines(db_session, sale.transaction_id)
    assert lines["5000"] == (Decimal("12.00"), Decimal("0"))
    assert lines["1300"] == (Decimal("0"), Decimal("12.00"))
    assert _bs_amount(client, "1300") == 28
    db_session.expire_all()
    assert db_session.get(Item, item["id"]).quantity_on_hand == 7
