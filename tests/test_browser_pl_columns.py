"""The Profit & Loss by month (#478), in playwright's Chromium on the
bakery's books: Columns → Month puts a column per month on P&L by Class's
grid (its header row named, the Account column frozen, Go to and ‹ Prev /
Next › for the months, a status a screen reader hears), the choice rides on
the address and the exports, the prior-year box is off while it is on, an
amount opens that account's lines for that month, and Back returns to the
grid by month.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_keyboard import _ax  # noqa: E402
from tests.test_browser_report_views import (  # noqa: E402,F401  (the fixtures)
    TITLE,
    _hash,
    _open_at,
    _query,
    browser_fixture,
    company_fixture,
    settle,
)

YEAR = ("2026-01-01", "2026-09-30")
URL = f"#/reports/profit-loss?start_date={YEAR[0]}&end_date={YEAR[1]}"


def test_columns_by_month_on_the_grid(browser, company, books):
    data = company.get(
        "/api/reports/profit-loss",
        params={"start_date": YEAR[0], "end_date": YEAR[1], "columns": "month"},
    ).json()
    page, handled = _open_at(browser, company, URL)
    try:
        page.wait_for_selector("#report-content table")
        assert _ax(page, "#report-columns-select")["name"].upper() == "COLUMNS"
        page.focus("#report-columns-select")
        page.select_option("#report-columns-select", "month")
        page.wait_for_selector("#report-content table.pivot-grid")
        settle(page, handled)
        assert _query(_hash(page))["columns"] == "month"
        heads = page.evaluate(
            "() => [...document.querySelectorAll('#report-content thead th')].map(th => th.textContent.trim())"
        )
        assert heads == ["Account"] + [c["name"] for c in data["columns"]] + ["Total"]
        assert heads[1:4] == ["Jan 2026", "Feb 2026", "Mar 2026"]
        assert _ax(page, "#grid-scroll")["name"] == "Profit & Loss by Month"
        assert page.evaluate("() => document.getElementById('cmp-prior').disabled")
        # Next › walks the months, said in the status line
        page.get_by_role("button", name="Next month").click()
        page.wait_for_function(
            "() => document.getElementById('grid-live').textContent.includes('Jan 2026')"
        )
        # the exports carry the choice
        exports = page.evaluate(
            "() => [...document.querySelectorAll('#report-content button')].map(b => b.getAttribute('onclick') || '').filter(t => t.includes('/api/reports/profit-loss/'))"
        )
        assert exports and all("columns=month" in t for t in exports)
        # the Net Income row's Total is the P&L's
        net = page.evaluate(
            """() => [...document.querySelectorAll('#report-content tbody tr')]
                .find(tr => tr.querySelector('th') && tr.querySelector('th').textContent.trim() === 'Net Income')
                .lastElementChild.textContent"""
        )
        assert float(net.replace("$", "").replace(",", "")) == data["total_net_income"]

        # an amount opens that account's lines for that month
        link = page.locator("#report-content tbody td[data-col] a").first
        col = data["columns"][int(link.evaluate("a => a.closest('td').dataset.col"))]
        link.click()
        page.wait_for_selector("#drilldown-body table")
        q = _query(_hash(page))
        assert q["from"] == "profit-loss"
        assert (q["start_date"], q["end_date"]) == (col["start_date"], col["end_date"])
        page.get_by_role("button", name="Back to Profit & Loss").click()
        page.wait_for_selector("#report-content table.pivot-grid")
        assert _query(_hash(page))["columns"] == "month"
        assert page.evaluate(TITLE) == "Profit & Loss"
    finally:
        page.close()
