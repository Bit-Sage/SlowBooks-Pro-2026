"""A bill line can be an amount alone, no quantity or rate (#391).

A utility bill or a pest-control bill has an amount, and the form asked for
a quantity and a rate on every line. A line may now send ``amount`` by
itself; it is stored as quantity 1 x rate = amount, so the journal, job
costing, the PDF and the exports read the line as they always have. A line
that sends a quantity or a rate is priced from them, and an amount sent
with them that disagrees by more than a cent is refused.
"""

from decimal import Decimal

from app.models.bills import Bill
from app.models.transactions import Transaction
from app.schemas.bills import BillLineCreate

D = Decimal


def _vendor(client, name="Orange County Utilities"):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _post(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-10-01", "lines": lines, **extra}
    return client.post("/api/bills", json=body)


def _balanced(db, bill_id, total):
    txn = db.get(Transaction, db.get(Bill, bill_id).transaction_id)
    dr = sum((ln.debit for ln in txn.lines), D("0"))
    cr = sum((ln.credit for ln in txn.lines), D("0"))
    assert dr == cr == D(total), (dr, cr)


def test_a_line_with_an_amount_alone_posts_that_amount(
    client, db_session, seed_accounts
):
    r = _post(
        client,
        _vendor(client),
        [
            {
                "account_id": seed_accounts["6000"].id,
                "description": "Electric, September",
                "amount": 125.50,
            }
        ],
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    (line,) = bill["lines"]
    assert (line["quantity"], line["rate"], line["amount"]) == (
        "1.00",
        "125.50",
        "125.50",
    )
    assert bill["subtotal"] == "125.50" and bill["total"] == "125.50"
    assert bill["balance_due"] == "125.50"
    _balanced(db_session, bill["id"], "125.50")


def test_a_quantity_and_rate_still_price_the_line(client, db_session, seed_accounts):
    v = _vendor(client, "Harbor Supply")
    acct = seed_accounts["6000"].id
    r = _post(client, v, [{"account_id": acct, "quantity": 2, "rate": 10}])
    assert r.status_code == 201, r.text
    (line,) = r.json()["lines"]
    assert (line["quantity"], line["rate"], line["amount"]) == (
        "2.00",
        "10.00",
        "20.00",
    )

    # an amount that agrees is accepted; one that does not is refused
    r = _post(
        client, v, [{"account_id": acct, "quantity": 2, "rate": 10, "amount": 20}]
    )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "20.00"
    r = _post(
        client, v, [{"account_id": acct, "quantity": 2, "rate": 10, "amount": 25}]
    )
    assert r.status_code == 422, r.text
    assert "amount 25.00 is not quantity x rate (20.00)" in r.text


def test_the_validator_rules_on_the_schema():
    # amount alone, or with the quantity and rate null or zero: 1 x amount
    for extra in ({}, {"quantity": None, "rate": None}, {"quantity": 0, "rate": 0}):
        ln = BillLineCreate(amount=125.5, **extra)
        assert (ln.quantity, ln.rate, ln.amount) == (1, 125.5, 125.5)
    # a sub-cent amount is kept to the cent, as the line is stored
    ln = BillLineCreate(amount=10.005)
    assert (ln.rate, ln.amount) == (10.01, 10.01)
    # nothing sent is a description-only line, as before
    ln = BillLineCreate(description="See attached")
    assert (ln.quantity, ln.rate, ln.amount) == (1, 0, 0)
    ln = BillLineCreate(quantity=0, rate=0, amount=0)
    assert (ln.quantity, ln.rate, ln.amount) == (0, 0, 0)
    # a quantity or a rate prices the line; the amount is derived
    ln = BillLineCreate(quantity=3, rate=1.5)
    assert ln.amount == 4.5
    ln = BillLineCreate(rate=40)
    assert (ln.quantity, ln.amount) == (1, 40)
    # a cent of disagreement is rounding; more is refused
    assert BillLineCreate(quantity=3, rate=1.333, amount=4).amount == 4.0
    for bad in ({"quantity": 3, "rate": 1.5, "amount": 5}, {"amount": -5}):
        try:
            BillLineCreate(**bad)
        except ValueError as e:
            assert "amount" in str(e)
        else:
            raise AssertionError(f"accepted {bad}")


def test_an_edit_takes_an_amount_only_line(client, db_session, seed_accounts):
    v = _vendor(client, "Bug Busters Pest Control")
    acct = seed_accounts["6000"].id
    r = _post(client, v, [{"account_id": acct, "quantity": 4, "rate": 30}])
    assert r.status_code == 201, r.text
    bill = r.json()
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {"account_id": acct, "description": "Quarterly service", "amount": 89},
                {"account_id": acct, "description": "Termite bond", "amount": 210.25},
            ]
        },
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(ln["quantity"], ln["rate"], ln["amount"]) for ln in got["lines"]] == [
        ("1.00", "89.00", "89.00"),
        ("1.00", "210.25", "210.25"),
    ]
    assert got["total"] == "299.25"
    _balanced(db_session, bill["id"], "299.25")
    # a header-only edit re-posts the stored lines as they are
    r = client.put(f"/api/bills/{bill['id']}", json={"terms": "Net 15"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == "299.25"


def test_a_bill_of_amount_only_lines_for_nothing_is_refused(
    client, db_session, seed_accounts
):
    r = _post(
        client,
        _vendor(client, "Nobody"),
        [{"account_id": seed_accounts["6000"].id, "amount": 0}],
    )
    assert r.status_code == 400, r.text
    assert "Enter the amount the vendor charged" in r.json()["detail"]


def test_the_form_takes_an_amount_alone():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/bills.js").read_text(
        encoding="utf-8"
    )
    row = js[js.index("    lineHtml(idx, line = null) {") :]
    row = row[: row.index("\n    },")]
    # Qty and Rate start blank; Amount is a box; a stored 1 x amount line is drawn as an amount
    assert 'class="line-qty" type="number" step="0.0001" value="${qty}"' in row
    assert 'class="line-amt" type="number" step="0.01" value="${amount}"' in row
    assert "Number(l.quantity) === 1 && Number(l.rate) === Number(l.amount)" in row
    save = js[js.index("    async save(e, id = null) {") :]
    save = save[: save.index("\n    },")]
    assert "BillsPage.priced(row)" in save
    assert "{ amount: parseFloat(row.querySelector('.line-amt')?.value) || 0 }" in save
    assert "an amount, or a quantity and rate" in save
