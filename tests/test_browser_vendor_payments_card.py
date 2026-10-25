"""A vendor's card says one thing about what was paid (#491), in a real
browser with the API stubbed: paid this year and all time count bill
payments and bank-feed entries both, and the section under it lists both,
so a vendor paid only through the feed no longer reads "Paid this year:
$57.00" over "No payments yet". With nothing paid it says so once."""

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

YEAR = dt.date.today().year
VENDOR = {"id": 7, "name": "QA Paper Co", "balance": "0", "is_active": True}


def _entry(tid, amount):
    return {
        "transaction_id": tid,
        "date": f"{YEAR}-03-0{tid % 9 + 1}",
        "account_name": "Checking",
        "memo": "",
        "amount": amount,
        "is_voided": False,
        "link": f"/#/banking/1?entry={tid}",
    }


def _card(browser, payments, entries):
    page = _open(browser, 1280, 900, "#/")
    page.route("**/api/vendors/7/bank-entries*", lambda r: r.fulfill(json=entries))
    page.route("**/api/vendors/7", lambda r: r.fulfill(json=VENDOR))
    page.route("**/api/bills?*", lambda r: r.fulfill(json=[]))
    page.route("**/api/bill-payments?*", lambda r: r.fulfill(json=payments))
    page.route("**/api/vendor-credits?*", lambda r: r.fulfill(json=[]))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=[]))
    page.evaluate("() => VendorsPage.showDetails(7)")
    page.wait_for_selector("text=Paid this year")
    text = page.evaluate("() => document.getElementById('modal-body').innerText")
    heads = page.evaluate(
        "() => [...document.querySelectorAll('#modal-body h4, #modal-body h5')].map(h => h.textContent.trim())"
    )
    page.close()
    return text, heads


def test_feed_entries_are_the_payments_the_paid_line_counts(browser):
    text, heads = _card(browser, [], [_entry(1, "45.00"), _entry(2, "12.00")])
    assert "Paid this year: $57.00" in text
    assert "No payments yet" not in text
    assert "Payments (2)" in heads
    assert "Bank and card entries (2)" in heads
    assert not any(h.startswith("Recent payments") for h in heads)


def test_nothing_paid_says_so_once(browser):
    text, heads = _card(browser, [], [])
    assert "Paid this year: $0.00" in text
    assert text.count("No payments yet") == 1
    assert "Payments (0)" in heads
