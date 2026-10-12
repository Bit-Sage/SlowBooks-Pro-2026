"""Analytics reads the ledger (issue #281).

Expenses by category summed bill lines on paid bills, and the revenue trend
paid invoices: a company that reconciles through a bank feed and records
the rest as journal entries saw an empty doughnut and an AI brief with
nothing in it. Both read the ledger now, as the Profit & Loss does.
"""

from datetime import date

from app.services import ai_tools
from app.services.analytics import AnalyticsEngine

THIS_MONTH = date.today().replace(day=1).isoformat()
TODAY = date.today().isoformat()


def _journal(client, seed_accounts, dr, cr, amount, when=TODAY, text="Entry"):
    r = client.post(
        "/api/journal",
        json={
            "date": when,
            "description": text,
            "lines": [
                {
                    "account_id": seed_accounts[dr].id,
                    "debit": str(amount),
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts[cr].id,
                    "debit": "0",
                    "credit": str(amount),
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _expenses(client):
    r = client.get(
        "/api/analytics/dashboard", params={"start_date": THIS_MONTH, "end_date": TODAY}
    )
    assert r.status_code == 200, r.text
    return r.json()["expenses_by_category"]


def test_a_journal_entry_and_a_register_entry_count_as_expenses(client, seed_accounts):
    """The reporter's company: no Bills, a bank feed and journal entries."""
    assert _expenses(client) == {}
    # the owner paid the hosting personally: expense against equity
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    # a categorised register entry: money out of Checking to an expense account
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": TODAY,
            "amount": "-120.00",
            "category_account_id": seed_accounts["6100"].id,
            "payee": "Corner Hardware",
        },
    )
    assert r.status_code == 201, r.text
    got = _expenses(client)
    assert got["6000"] == 48.0 and got["6100"] == 120.0, got


def test_an_unpaid_bill_counts_when_dated_and_a_void_nets_out(client, seed_accounts):
    """Accrual, as the P&L: a bill is an expense when it is dated, paid or
    not; an expense voided is gone."""
    vendor = client.post("/api/vendors", json={"name": "Pier Lumber"})
    assert vendor.status_code == 201, vendor.text
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.json()["id"],
            "date": TODAY,
            "lines": [
                {
                    "description": "Lumber",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": "300.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert _expenses(client)["6000"] == 300.0  # unpaid, and counted
    ex = client.post(
        "/api/expenses",
        json={
            "date": TODAY,
            "payee": "Corner Hardware",
            "expense_account_id": seed_accounts["6100"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "42.50",
        },
    )
    assert ex.status_code == 201, ex.text
    assert _expenses(client)["6100"] == 42.5
    assert client.post(f"/api/expenses/{ex.json()['id']}/void").status_code == 200
    assert "6100" not in _expenses(client)  # the reversal nets it to zero


def test_the_revenue_trend_reads_income_postings(client, seed_accounts):
    """Income booked by a journal entry (a deposit a bank-fed company
    records straight to the ledger) shows in the trend's month."""
    key = date.today().strftime("%Y-%m")
    before = client.get("/api/analytics/dashboard").json()["revenue_trend"]
    assert before[key] == 0.0
    _journal(
        client, seed_accounts, "1000", "4000", "2500.00", text="Deposit, consulting"
    )
    after = client.get("/api/analytics/dashboard").json()["revenue_trend"]
    assert after[key] == 2500.0
    assert len(after) == 12 and list(after)[-1] == key


def test_revenue_by_customer_is_still_paid_invoices(client, seed_accounts):
    """Ledger lines carry no customer: that table keeps its paid basis, and
    the page says so (tests/test_analytics_basis.py pins the labels)."""
    _journal(client, seed_accounts, "1000", "4000", "2500.00")
    dash = client.get(
        "/api/analytics/dashboard", params={"start_date": THIS_MONTH, "end_date": TODAY}
    ).json()
    assert dash["revenue_by_customer"] == {}


def test_the_ai_tool_answers_from_the_same_ledger(client, db_session, seed_accounts):
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    _journal(
        client, seed_accounts, "6100", "3000", "12.00", when="2024-01-15", text="Old"
    )
    got = ai_tools.get_expenses_by_category(db_session, THIS_MONTH, TODAY)
    assert got["total"] == 48.0
    assert [r["category"] for r in got["results"]] == [
        f"6000 {seed_accounts['6000'].name}"
    ]
    everything = ai_tools.get_expenses_by_category(db_session)
    assert everything["total"] == 60.0  # no dates: all time, as before
    # the engine's own rows, shared by both
    rows = AnalyticsEngine(db_session).expenses_by_account(
        date.fromisoformat(THIS_MONTH), date.today()
    )
    assert [(n, a) for n, _, a in rows] == [("6000", 48.0)]
