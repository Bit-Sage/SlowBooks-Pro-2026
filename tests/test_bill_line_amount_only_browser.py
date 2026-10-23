"""Enter Bill on the screen, the amount-alone line (#391, reviewer #401).

Typing a quantity on a line drawn as an amount alone used to multiply a
blank rate to zero; the quantity now seeds the rate from the amount, so the
line prices at quantity x amount and Save posts that. A stock item drawn
from a stored 1 x amount line shows the quantity 1 it is received as.

Reuses the contrast sweep's harness. Skipped, as one module, where
playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)


def _amt(page, row):
    return page.locator(f"#bill-lines tr:nth-child({row}) .line-amt").input_value()


def _total(page):
    txt = page.locator("#bill-total").inner_text()
    return txt.replace("$", "").replace(",", "")


def test_typing_a_quantity_on_an_amount_alone_line_prices_it(
    browser, company, books, client, seed_accounts
):
    acct = seed_accounts["6000"].id
    # a $740 bill: a $640 amount-alone line and a 4 x 25 priced line
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": books["vendor"],
            "date": "2026-10-01",
            "bill_number": "AMT-740",
            "lines": [
                {"account_id": acct, "description": "Rent, October", "amount": 640},
                {
                    "account_id": acct,
                    "description": "Supplies",
                    "quantity": 4,
                    "rate": 25,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill_id = r.json()["id"]

    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/bills")
        page.evaluate(f"async () => {{ await BillsPage.showForm({bill_id}); }}")
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        # the amount-alone line draws with Qty and Rate blank, Amount $640
        assert page.locator("#bill-lines tr:nth-child(1) .line-qty").input_value() == ""
        assert (
            page.locator("#bill-lines tr:nth-child(1) .line-rate").input_value() == ""
        )
        assert _amt(page, 1) == "640.00"

        # type Qty 2: the rate seeds from the amount, so the line is 2 x 640
        page.fill("#bill-lines tr:nth-child(1) .line-qty", "2")
        settle(page, handled)
        assert _amt(page, 1) == "1280.00"
        assert float(_total(page)) == 1380.00

        page.evaluate("() => document.getElementById('bill-form').requestSubmit()")
        settle(page, handled)
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        after = client.get(f"/api/bills/{bill_id}").json()
        assert after["total"] == "1380.00"
        assert (after["lines"][0]["quantity"], after["lines"][0]["rate"]) == (
            "2.00",
            "640.00",
        )
    finally:
        page.close()


def test_a_quantity_then_cleared_leaves_the_amount(browser, company, books, client):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/bills")
        page.evaluate("async () => { await BillsPage.showForm(); }")
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        # a fresh line, typed as an amount alone
        page.fill("#bill-lines tr:nth-child(1) .line-amt", "125.50")
        settle(page, handled)
        # Qty 1 prices it at 1 x 125.50, not 0
        page.fill("#bill-lines tr:nth-child(1) .line-qty", "1")
        settle(page, handled)
        assert _amt(page, 1) == "125.50"
        # clearing the Qty brings the amount back, not blank or 0
        page.fill("#bill-lines tr:nth-child(1) .line-qty", "")
        settle(page, handled)
        assert _amt(page, 1) == "125.50"
        assert (
            page.locator("#bill-lines tr:nth-child(1) .line-rate").input_value() == ""
        )
    finally:
        page.close()


def test_a_stock_item_amount_alone_line_shows_its_received_quantity(
    browser, company, books, client
):
    # a stock item (Sourdough Loaf) entered as an amount alone: stored 1 x 8.5
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": books["vendor"],
            "date": "2026-10-01",
            "bill_number": "AMT-STOCK",
            "lines": [{"item_id": books["loaf"], "amount": 8.5}],
        },
    )
    assert r.status_code == 201, r.text
    bill_id = r.json()["id"]
    assert r.json()["lines"][0]["quantity"] == "1.00"

    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/bills")
        page.evaluate(f"async () => {{ await BillsPage.showForm({bill_id}); }}")
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        # the received quantity 1 is shown, not hidden by a blank Qty box
        assert (
            page.locator("#bill-lines tr:nth-child(1) .line-qty").input_value() == "1"
        )
        assert (
            page.locator("#bill-lines tr:nth-child(1) .line-rate").input_value()
            == "8.5"
        )
        assert _amt(page, 1) == "8.50"
    finally:
        page.close()
