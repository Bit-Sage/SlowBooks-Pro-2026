"""The register's "+ Add a new account…" in a real browser (#386): the
panel opens under the category picker, the account is posted, and the
picker shows it chosen with the cursor on the amount. The API is stubbed;
the page is the real one (tests/test_browser_ui.py's fixtures)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

REGISTER = {
    "account_name": "Checking",
    "account_number": "1000",
    "bank_kind": "bank",
    "entries": [],
    "balance": 0,
    "opening_balance": 0,
    "class_name": None,
}
CHART = [
    {
        "id": 1,
        "name": "Checking",
        "account_number": "1000",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {
        "id": 2,
        "name": "Automobile",
        "account_number": "6100",
        "account_type": "expense",
    },
]


def test_a_new_account_is_made_from_the_entry_row(browser):
    posted = []

    def _accounts(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(
                status=201, json={**posted[-1], "id": 77, "is_active": True}
            )
        return route.fulfill(json=CHART)

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", _accounts)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-category")
        assert (
            page.locator("#qe-category option").nth(1).inner_text()
            == "+ Add a new account…"
        )
        assert not page.is_visible("#qe-category-new")

        page.select_option("#qe-category", "__new__")
        page.wait_for_selector("#qe-category-new", state="visible")
        assert (
            page.evaluate("() => document.activeElement.id") == "qe-category-new-name"
        )
        page.fill("#qe-category-new-name", "Truck Fuel")
        # the parents offered are the expense accounts, not the bank
        assert page.locator("#qe-category-new-parent option").all_inner_texts() == [
            "None",
            "6100 - Automobile",
        ]
        page.select_option("#qe-category-new-parent", "2")
        page.click("text=Add account")
        page.wait_for_function(
            "() => document.getElementById('qe-category').value === '77'"
        )

        assert posted == [
            {
                "name": "Truck Fuel",
                "account_type": "expense",
                "parent_id": 2,
                "account_number": None,
            }
        ]
        assert page.locator("#qe-category option:checked").inner_text() == "Truck Fuel"
        assert not page.is_visible("#qe-category-new")
        assert page.evaluate("() => document.activeElement.id") == "qe-out"
    finally:
        page.close()


def test_a_payee_not_on_the_vendor_list_is_offered_as_one(browser):
    posted = []

    def _vendors(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 5})
        return route.fulfill(json=[{"id": 1, "name": "Acme Supply"}])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/vendors*", _vendors)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")
        # a vendor's name, whatever its case: no question
        page.fill("#qe-payee", "acme supply")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0

        page.fill("#qe-payee", "Harbor Hardware")
        page.press("#qe-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert toast.inner_text().split() == [
            "Add",
            '"Harbor',
            'Hardware"',
            "as",
            "a",
            "vendor?",
            "Add",
            "Not",
            "now",
        ]
        # the row keeps its text and the question stays out of the way
        assert page.input_value("#qe-payee") == "Harbor Hardware"
        toast.locator("button", has_text="Not now").click()
        assert page.locator("#toast-container .toast").count() == 0
        # answered: not asked again for this name
        page.focus("#qe-payee")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0

        page.fill("#qe-payee", "Pine Street Lumber")
        page.press("#qe-payee", "Tab")
        page.locator("#toast-container .toast button", has_text="Add").click()
        page.wait_for_selector("#toast-container .toast", state="attached")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('added to Vendors'))"
        )
        assert posted == [{"name": "Pine Street Lumber"}]
    finally:
        page.close()
