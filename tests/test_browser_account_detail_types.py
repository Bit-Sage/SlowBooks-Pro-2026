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
