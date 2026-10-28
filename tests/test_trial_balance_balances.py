"""#597: the Trial Balance reads as QuickBooks does: balance-sheet accounts
at their balance as of the end date, income and expense accounts for the
period, with a derived row for earlier periods' net income so it foots."""

from decimal import Decimal

import pytest

from app.services.ledger_exports import rows_of

PERIOD = "start_date=2026-01-01&end_date=2026-12-31"


def _je(client, accts, when, dr, cr, amount):
    r = client.post(
        "/api/journal",
        json={
            "date": when,
            "description": "tb",
            "lines": [
                {"account_id": accts[dr].id, "debit": amount},
                {"account_id": accts[cr].id, "credit": amount},
            ],
        },
    )
    assert r.status_code == 201, r.text


@pytest.fixture
def books(client, seed_accounts):
    _je(client, seed_accounts, "2025-12-15", "1000", "4000", "500.00")
    _je(client, seed_accounts, "2025-12-20", "6000", "1000", "100.00")
    _je(client, seed_accounts, "2026-02-01", "1000", "4000", "1200.00")
    _je(client, seed_accounts, "2026-03-05", "6000", "1000", "345.50")
    return seed_accounts


def _by_number(tb):
    return {i["account_number"]: i for i in tb["items"]}


def test_bank_at_its_balance_and_expense_for_the_period(client, books):
    tb = client.get(f"/api/reports/trial-balance?{PERIOD}").json()
    rows = _by_number(tb)
    assert rows["1000"]["total_debit"] == 1254.50  # 500 - 100 + 1200 - 345.50
    assert rows["1000"]["total_credit"] == 0
    assert rows["6000"]["total_debit"] == 345.50  # this year only
    assert rows["4000"]["total_credit"] == 1200.00


def test_it_foots_with_the_prior_periods_net_income_row(client, books):
    tb = client.get(f"/api/reports/trial-balance?{PERIOD}").json()
    prior = [i for i in tb["items"] if i["account_id"] is None]
    assert len(prior) == 1 and prior[0]["account_name"].endswith("(prior periods)")
    assert prior[0]["total_credit"] == 400.00  # 500 income - 100 expense
    assert tb["total_debit"] == tb["total_credit"] == 1600.00
    assert tb["difference"] == 0


def test_no_prior_activity_adds_no_row(client, seed_accounts):
    _je(client, seed_accounts, "2026-02-01", "1000", "4000", "75.00")
    tb = client.get(f"/api/reports/trial-balance?{PERIOD}").json()
    assert all(i["account_id"] for i in tb["items"])
    assert tb["total_debit"] == tb["total_credit"] == 75.00


def test_the_pdf_and_csv_carry_the_same_figures(client, books):
    tb = client.get(f"/api/reports/trial-balance?{PERIOD}").json()
    assert client.get(f"/api/reports/trial-balance/pdf?{PERIOD}").status_code == 200
    r = client.get(f"/api/reports/trial-balance/csv?{PERIOD}")
    assert r.status_code == 200
    rows = rows_of(r.text)
    body, total = rows[1:-1], rows[-1]
    assert len(body) == len(tb["items"])
    bank = next(row for row in body if row[0] == "1000")
    assert Decimal(bank[3]) == Decimal("1254.50")
    assert total[3] == total[4] == "1600.00"
