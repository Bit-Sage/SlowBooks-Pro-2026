"""Feed Add offers the payee as a vendor in a real browser (#491): a line
added from To review whose payee no vendor has gets the register's "Add X
as a vendor?" toast, and its Add makes the vendor through
/api/banking/transactions/vendors. A payee the server matched to a vendor is
not asked about. The API is stubbed; the page is the real one."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

OVERVIEW = [{"account_id": 1, "name": "Checking", "feed": {"bank_account_id": 5}}]


def _line(id_, payee, **extra):
    return {
        "id": id_,
        "bank_account_id": 5,
        "date": "2026-09-03",
        "amount": -450.0,
        "payee": payee,
        "description": "",
        "check_number": None,
        "category_account_id": 2,
        "category_name": "Automobile",
        "match_status": "unmatched",
        "transaction_id": None,
        "transaction_line_id": None,
        "import_source": "ofx",
        "reconciled": False,
        "created_at": "2026-09-03T00:00:00",
        **extra,
    }


def _page(browser, lines, vendor_for):
    calls = []

    def _transactions(route):
        url = route.request.url
        if route.request.method == "POST" and url.endswith("/vendors"):
            calls.append(("vendors", route.request.post_data_json))
            return route.fulfill(
                json={"created": [{"id": 40, "name": "NT Suites"}], "linked": 1}
            )
        if route.request.method == "POST" and url.endswith("/add"):
            line_id = int(url.rsplit("/", 2)[-2])
            calls.append(("add", line_id))
            line = next(t for t in lines if t["id"] == line_id)
            name = vendor_for.get(line_id)
            return route.fulfill(
                json={
                    **line,
                    "match_status": "added",
                    "transaction_id": 100 + line_id,
                    "vendor_id": 7 if name else None,
                    "vendor_name": name,
                }
            )
        first = "status=unmatched" in url and "skip=0" in url
        return route.fulfill(json=lines if first else [])

    page = _open(browser, 1280, 800, "#/")
    page.evaluate("() => { window.__payeeToastMs = 60000; }")
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/banking/overview*", lambda r: r.fulfill(json=OVERVIEW))
    page.route("**/api/banking/transactions**", _transactions)
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("text=To review")
    return page, calls


def test_feed_add_offers_a_new_payee_as_a_vendor_and_makes_it(browser):
    page, calls = _page(browser, [_line(9, "NT Suites")], {})
    try:
        page.click('button[onclick^="BankingPage.addLine"]')
        toast = page.wait_for_selector('text=Add "NT Suites" as a vendor?')
        assert toast
        page.locator("#toast-container button", has_text="Add").first.click()
        page.wait_for_selector("text=NT Suites added to Vendors")
        assert ("add", 9) in calls
        assert ("vendors", {"line_ids": [9]}) in calls
    finally:
        page.close()


def test_a_payee_matched_to_a_vendor_is_not_asked_about(browser):
    page, calls = _page(browser, [_line(9, "City Water")], {9: "City Water"})
    try:
        page.click('button[onclick^="BankingPage.addLine"]')
        page.wait_for_selector("text=Added to the books for City Water")
        assert page.locator("text=as a vendor?").count() == 0
        assert not [c for c in calls if c[0] == "vendors"]
    finally:
        page.close()
