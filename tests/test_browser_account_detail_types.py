"""The Chart of Accounts by detail type (#390), in playwright's Chromium on
the bakery's books of tests/test_theme_contrast.py:

- under each type heading, a heading per detail type, and the Type column
  says the detail type ("Long Term Liability");
- the New Account form has one picker, a group per base type with
  QuickBooks' types inside and no Accounts Receivable / Payable; choosing
  Fixed Asset saves an asset of that detail type;
- a control account's form shows its detail type, locked.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

ROWS = """() => [...document.querySelectorAll('#page-content tbody tr')]
    .map(r => r.textContent.replace(/\\s+/g, ' ').trim())"""


def test_the_chart_groups_by_detail_type_and_the_form_picks_one(
    browser, company, books
):
    accounts = {a["account_number"]: a for a in company.get("/api/accounts").json()}
    page, handled = _open_at(browser, company, "#/accounts")
    try:
        page.wait_for_selector("#page-content tbody tr")
        rows = page.evaluate(ROWS)
        liabilities = rows.index("Liabilities")
        long_term = rows.index("Long Term Liability")
        loan = next(i for i, r in enumerate(rows) if r.startswith("2400"))
        assert liabilities < long_term < loan < rows.index("Equity")
        assert "Long Term Liability" in rows[loan]
        assert rows.index("Fixed Asset") < next(
            i for i, r in enumerate(rows) if r.startswith("1500")
        )

        page.get_by_role("button", name="New Account").click()
        page.wait_for_selector("select[name=detail_type]")
        groups = page.evaluate(
            """() => [...document.querySelectorAll('select[name=detail_type] optgroup')]
                .map(g => [g.label, [...g.children].map(o => o.textContent)])"""
        )
        assert [g for g, _ in groups] == [
            "Assets",
            "Liabilities",
            "Equity",
            "Income",
            "Cost of Goods Sold",
            "Expenses",
        ]
        assert dict(groups)["Assets"] == [
            "Bank",
            "Other Current Asset",
            "Fixed Asset",
            "Other Asset",
        ]
        assert dict(groups)["Expenses"] == ["Expense", "Other Expense"]
        assert page.eval_on_selector("select[name=detail_type]", "s => s.value") == (
            "expense"
        )
        page.fill("input[name=name]", "Bread oven")
        page.select_option("select[name=detail_type]", "fixed_asset")
        page.click("form button[type=submit]")
        page.wait_for_function(
            "() => document.getElementById('modal-overlay').classList.contains('hidden')"
        )
        settle(page, handled)
        made = next(
            a for a in company.get("/api/accounts").json() if a["name"] == "Bread oven"
        )
        assert (made["account_type"], made["detail_type"], made["bank_kind"]) == (
            "asset",
            "fixed_asset",
            None,
        )

        # a control account: its detail type shown, and locked
        page.evaluate(f"App.showAccountForm({accounts['1100']['id']})")
        page.wait_for_selector("select[name=detail_type]")
        sel = page.locator("select[name=detail_type]")
        assert sel.is_disabled()
        assert sel.evaluate("s => s.selectedOptions[0].textContent") == (
            "Accounts Receivable"
        )
    finally:
        page.close()


TABLE_ROWS = """() => [...document.querySelectorAll('#report-content tbody tr')]
    .map(r => r.textContent.replace(/\\s+/g, ' ').trim())"""


def _account(company, **body):
    r = company.post("/api/accounts", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _journal(company, day, debit, credit, amount):
    r = company.post(
        "/api/journal",
        json={
            "date": day,
            "description": "statements",
            "lines": [
                {"account_id": debit, "debit": amount, "credit": "0"},
                {"account_id": credit, "debit": "0", "credit": amount},
            ],
        },
    )
    assert r.status_code == 201, r.text


def test_the_statements_print_the_groups_and_the_families(browser, company, books):
    """PR 2 (#390) and #416 on the screen: the Balance Sheet's Fixed Assets
    and Long Term Liabilities groups with their subtotals; the P&L's Other
    Income below Net Operating Income, and a sub-account beneath its parent
    with the family's total."""
    accounts = {a["account_number"]: a for a in company.get("/api/accounts").json()}
    cash = accounts["1000"]["id"]
    oven = _account(
        company,
        name="Deck oven",
        account_number="1560",
        account_type="asset",
        detail_type="fixed_asset",
    )
    note = _account(
        company,
        name="Oven note",
        account_number="2710",
        account_type="liability",
        detail_type="long_term_liability",
    )
    travel = _account(
        company, name="Travel", account_number="7300", account_type="expense"
    )
    hotel = _account(
        company,
        name="Hotel",
        account_number="7310",
        account_type="expense",
        parent_id=travel["id"],
    )
    _journal(company, "2026-09-10", oven["id"], note["id"], "8000.00")
    _journal(company, "2026-09-11", cash, accounts["4900"]["id"], "45.00")
    _journal(company, "2026-09-12", hotel["id"], cash, "120.00")

    page, _handled = _open_at(
        browser, company, "#/reports/balance-sheet?as_of_date=2026-09-30"
    )
    try:
        page.wait_for_selector("#report-content table")
        rows = page.evaluate(TABLE_ROWS)
        fixed = rows.index("Fixed Assets")
        total_fixed = next(
            i for i, r in enumerate(rows) if r.startswith("Total Fixed Assets")
        )
        oven_row = next(
            i for i, r in enumerate(rows) if r.startswith("1560 - Deck oven")
        )
        assert (
            fixed
            < oven_row
            < total_fixed
            < rows.index(next(r for r in rows if r.startswith("Total Assets")))
        )
        long_term = rows.index("Long Term Liabilities")
        assert long_term < next(
            i for i, r in enumerate(rows) if r.startswith("2710 - Oven note")
        )
        assert any(r.startswith("Total Long Term Liabilities") for r in rows)
    finally:
        page.close()

    page, _handled = _open_at(
        browser,
        company,
        "#/reports/profit-loss?start_date=2026-09-01&end_date=2026-09-30",
    )
    try:
        page.wait_for_selector("#report-content table")
        rows = page.evaluate(TABLE_ROWS)
        travel_at = rows.index("7300 - Travel")
        assert rows[travel_at + 1].startswith("7310 - Hotel")
        assert rows[travel_at + 2].startswith("Total 7300 - Travel")
        noi = next(
            i for i, r in enumerate(rows) if r.startswith("Net Operating Income")
        )
        other = rows.index("Other Income")
        assert (
            rows.index(next(r for r in rows if r.startswith("Total Expenses")))
            < noi
            < other
        )
        assert any(r.startswith("4900 - Other Income") for r in rows[other:])
        assert rows[-1].startswith("Net Income")
    finally:
        page.close()


STATS = """() => [...document.querySelectorAll('#class-stats > *')]
    .map(e => e.textContent.replace(/\\s+/g, ' ').trim())"""


def test_the_classes_pages_count_other_income_with_income(browser, company, books):
    """The Classes list keeps four figures that add to net, Other Income
    (#390) counted with income for display. The class page's cards are
    operating (Income, Cost of goods, Gross profit, Expenses) with a Net
    other income card beside them, so Income - Cost of goods = Gross profit
    (the #429 round)."""
    accounts = {a["account_number"]: a for a in company.get("/api/accounts").json()}
    _journal(
        company,
        "2026-09-15",
        accounts["1000"]["id"],
        accounts["4900"]["id"],
        "1234.00",
    )
    span = "start_date=2026-09-01&end_date=2026-09-30&period=custom"
    act = company.get(
        "/api/classes/activity?start_date=2026-09-01&end_date=2026-09-30"
    ).json()
    row = next(c for c in act["classes"] if c["other_income"])
    shown = row["income"] + row["other_income"]
    page, _handled = _open_at(browser, company, f"#/classes?{span}&show=all")
    try:
        page.wait_for_selector("#page-content tbody tr")
        rows = page.evaluate(
            """() => [...document.querySelectorAll('#page-content tbody tr')]
                .map(r => [...r.cells].map(c => c.textContent.trim()))"""
        )
        mine = next(r for r in rows if r[0].startswith(row["name"]))
        assert mine[2] == f"${shown:,.2f}"
    finally:
        page.close()
    page, _handled = _open_at(browser, company, f"#/classes/{row['id']}?{span}")
    try:
        page.wait_for_selector("#class-stats")
        stats = " | ".join(page.evaluate(STATS))
        assert f"Income ${row['income']:,.2f}" in stats
        other = row["other_income"] - row.get("other_expense", 0)
        assert f"Net other income ${other:,.2f}" in stats
        assert f"${shown:,.2f}" not in stats
    finally:
        page.close()
