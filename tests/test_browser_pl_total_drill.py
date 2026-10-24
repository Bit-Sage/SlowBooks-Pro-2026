"""The P&L's totals open the transactions behind them (#479), in
playwright's Chromium on the bakery's books: Total Income and Net Income are
links named for the total that the keyboard reaches; Enter opens the set's
drill-down on its own address (section=, the statement's dates, from=), its
total is the statement's, and its "Back to Profit & Loss" returns with the
keyboard on the total it left. P&L by Job's subtotals open one job's set.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_keyboard import _ax  # noqa: E402
from tests.test_browser_report_views import (  # noqa: E402,F401  (the fixtures)
    PL_URL,
    SEPT,
    TITLE,
    _hash,
    _open_at,
    _query,
    browser_fixture,
    company_fixture,
    settle,
)


def _money(page, selector):
    text = page.evaluate(f"() => document.querySelector('{selector}').textContent")
    return float(text.replace("$", "").replace(",", "").strip())


def test_a_pl_total_opens_its_transactions_and_back_returns_to_it(
    browser, company, books
):
    pl = company.get(
        "/api/reports/profit-loss", params={"start_date": SEPT[0], "end_date": SEPT[1]}
    ).json()
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        for key in ("set:income", "set:gross_profit", "set:expenses", "set:net_income"):
            sel = f'#report-content a[data-row-key="{key}"]'
            assert page.locator(sel).count() == 1, key
            assert _ax(page, sel)["role"] == "link"
        sel = '#report-content a[data-row-key="set:income"]'
        assert _ax(page, sel)["name"] == "Total Income"
        page.focus(sel)
        page.keyboard.press("Enter")
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        assert page.evaluate(TITLE) == "Drill-down — Total Income"
        q = _query(_hash(page))
        assert (q["section"], q["start_date"], q["end_date"], q["from"]) == (
            "income",
            *SEPT,
            "profit-loss",
        )
        total = _money(page, "#drilldown-body tbody tr:last-child td.amount")
        assert total == pl["total_operating_income"]

        page.get_by_role("button", name="Back to Profit & Loss").focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#report-content table")
        settle(page, handled)
        assert _hash(page) == PL_URL
        page.wait_for_function(
            f"() => document.activeElement && document.activeElement.matches('{sel}')"
        )

        # Net Income: costs subtract, the running total ends on the figure
        page.click('#report-content a[data-row-key="set:net_income"]')
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        assert page.evaluate(TITLE) == "Drill-down — Net Income"
        total = _money(page, "#drilldown-body tbody tr:last-child td.amount")
        assert total == pl["net_income"]
        last = page.evaluate(
            "() => [...document.querySelectorAll('#drilldown-body tbody tr')].slice(-2)[0]"
            ".querySelectorAll('td.amount')[1].textContent"
        )
        assert float(last.replace("$", "").replace(",", "")) == pl["net_income"]
    finally:
        page.close()


def test_the_address_of_a_total_reopens_it(browser, company, books):
    url = (
        f"#/reports/account-transactions?section=gross_profit"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}&from=profit-loss"
    )
    pl = company.get(
        "/api/reports/profit-loss", params={"start_date": SEPT[0], "end_date": SEPT[1]}
    ).json()
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#drilldown-body table")
        assert page.evaluate(TITLE) == "Drill-down — Gross Profit"
        assert page.get_by_role("button", name="Back to Profit & Loss").count() == 1
        total = _money(page, "#drilldown-body tbody tr:last-child td.amount")
        assert total == pl["operating_gross_profit"]
    finally:
        page.close()
