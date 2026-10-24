"""The Profit & Loss Detail on the screen (#477), in playwright's Chromium on
the bakery's books: the address reproduces it, its table is named and its
columns are QuickBooks', its figures are the server's, a line's Type is a
link named for the line that the keyboard reaches and opens, and Back from
the document returns to the report with the keyboard on that line.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_keyboard import _ax  # noqa: E402
from tests.test_browser_report_views import (  # noqa: E402,F401  (the fixtures)
    MODAL_SHOWN,
    SEPT,
    TITLE,
    _hash,
    _open_at,
    browser_fixture,
    company_fixture,
    settle,
)

URL = f"#/reports/profit-loss-detail?start_date={SEPT[0]}&end_date={SEPT[1]}"


def test_the_detail_opens_on_its_address_and_a_line_opens_its_document(
    browser, company, books
):
    data = company.get(
        "/api/reports/profit-loss-detail",
        params={"start_date": SEPT[0], "end_date": SEPT[1]},
    ).json()
    assert data["line_count"] > 0
    page, handled = _open_at(browser, company, URL)
    try:
        page.wait_for_selector("#report-content table.pl-detail")
        assert page.evaluate(TITLE) == "Profit & Loss Detail"
        heads = page.evaluate(
            "() => [...document.querySelectorAll('#report-content thead th')].map(th => th.textContent)"
        )
        assert heads == [
            "Type",
            "Date",
            "Num",
            "Name",
            "Memo",
            "Split",
            "Amount",
            "Balance",
        ]
        caption = page.evaluate(
            "() => document.querySelector('#report-content table.pl-detail caption').textContent"
        )
        assert caption.startswith("Profit & Loss Detail")
        text = page.evaluate(
            "() => document.getElementById('report-content').textContent"
        )
        for label in ("Total Income", "Gross Profit", "Total Expenses", "Net Income"):
            assert label in text
        net = page.evaluate(
            """() => [...document.querySelectorAll('#report-content tr.pl-detail-total')]
                .find(tr => tr.textContent.trim().startsWith('Net Income'))
                .querySelector('td.amount').textContent"""
        )
        assert (
            net.replace("$", "").replace(",", "").strip() == f"{data['net_income']:.2f}"
        )

        # a line's Type: a link the keyboard reaches, named for the line
        first = next(
            e
            for blocks in data["sections"].values()
            for b in blocks
            for e in b["entries"]
        )
        sel = f'#report-content a[data-row-key="line:{first["line_id"]}"]'
        ax = _ax(page, sel)
        assert ax["role"] == "link"
        assert ax["name"].startswith(first["type"])
        page.focus(sel)
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => !location.hash.startsWith('#/reports/profit-loss-detail')"
        )
        assert _hash(page).startswith(
            first["source_link"].replace("/#", "#").rsplit("/", 1)[0]
        )
        page.wait_for_function(
            "() => !document.querySelector('#report-content table.pl-detail')"
        )
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)

        page.go_back()
        page.wait_for_selector("#report-content table.pl-detail")
        settle(page, handled)
        assert _hash(page) == URL
        page.wait_for_function(
            f"() => document.activeElement && document.activeElement.matches('{sel}')"
        )
    finally:
        page.close()


def test_the_class_and_job_pickers_ride_on_the_address(browser, company, books):
    page, handled = _open_at(browser, company, URL)
    try:
        page.wait_for_selector("#report-content table.pl-detail")
        # the class and job pickers take the type-ahead (combobox.js): the
        # box is what the keyboard and a screen reader reach, named by its label
        for sel, name in (("#pld-class-box", "CLASS"), ("#pld-job-box", "JOB")):
            ax = _ax(page, sel)
            assert (ax["role"], ax["name"].upper()) == ("combobox", name), sel
        page.select_option("#pld-job", "0", force=True)
        page.wait_for_function("() => location.hash.includes('job_id=0')")
        page.wait_for_selector("#report-content table.pl-detail")
        text = page.evaluate(
            "() => document.getElementById('report-content').textContent"
        )
        assert "Job: No job only" in text
    finally:
        page.close()
