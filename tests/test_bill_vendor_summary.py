"""Enter Bill shows the picked vendor's summary beside the form (#601)."""

from decimal import Decimal
from pathlib import Path

from app.models.contacts import Vendor

ROOT = Path(__file__).resolve().parent.parent


def _vendor(db_session, name="Cascade Flour Mill", notes=None):
    v = Vendor(name=name, is_active=True, notes=notes)
    db_session.add(v)
    db_session.commit()
    return v.id


def _bill(client, vid, expense, number, amount, date="2026-09-01"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "bill_number": number,
            "date": date,
            "lines": [{"account_id": expense, "description": "x", "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, vid, bank, bill_id, amount, date, check=None):
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vid,
            "date": date,
            "amount": amount,
            "method": "Check",
            "check_number": check,
            "pay_from_account_id": bank,
            "allocations": [{"bill_id": bill_id, "amount": amount}],
        },
    )
    assert r.status_code == 201, r.text


def test_summary_balance_matches_the_vendor_page(client, db_session, seed_accounts):
    vid = _vendor(db_session, notes="Net 15, call ahead")
    expense, bank = seed_accounts["6000"].id, seed_accounts["1000"].id
    b = _bill(client, vid, expense, "B-1", 200)
    _pay(client, vid, bank, b["id"], 75, "2026-09-05", check="1042")

    s = client.get(f"/api/vendors/{vid}/summary").json()
    page = client.get(f"/api/vendors/{vid}").json()
    assert Decimal(s["balance"]) == Decimal("125.00") == Decimal(str(page["balance"]))
    assert s["notes"] == "Net 15, call ahead"
    assert s["bills"][0]["bill_number"] == "B-1"
    assert s["bills"][0]["balance_due"] == "125.00"
    assert s["bills"][0]["status"] == "partial"
    assert s["payments"][0]["amount"] == "75.00"
    assert s["payments"][0]["check_number"] == "1042"


def test_summary_lists_the_last_five_newest_first(client, db_session, seed_accounts):
    vid = _vendor(db_session)
    expense, bank = seed_accounts["6000"].id, seed_accounts["1000"].id
    for n in range(1, 8):
        b = _bill(client, vid, expense, f"B-{n}", 10, date=f"2026-09-0{n}")
        _pay(client, vid, bank, b["id"], 5, f"2026-09-1{n}")

    s = client.get(f"/api/vendors/{vid}/summary").json()
    assert [b["bill_number"] for b in s["bills"]] == [f"B-{n}" for n in range(7, 2, -1)]
    assert [p["date"] for p in s["payments"]] == [
        f"2026-09-1{n}" for n in range(7, 2, -1)
    ]


def test_a_new_vendor_has_an_empty_summary(client, db_session, seed_accounts):
    vid = _vendor(db_session)
    s = client.get(f"/api/vendors/{vid}/summary").json()
    assert Decimal(s["balance"]) == 0
    assert s["bills"] == [] and s["payments"] == [] and s["notes"] is None


def test_summary_of_an_unknown_vendor_is_404(client):
    assert client.get("/api/vendors/99999/summary").status_code == 404


def test_enter_bill_has_the_panel_and_asks_for_the_summary():
    js = (ROOT / "app/static/js/bills.js").read_text(encoding="utf-8")
    assert 'id="bill-vendor-summary"' in js
    assert "/vendors/${id}/summary" in js
    # a vendor pick (and the edit form's load) asks for it
    assert "BillsPage.showVendorSummary(vendorId);" in js
    assert "BillsPage.showVendorSummary(bill.vendor_id);" in js
