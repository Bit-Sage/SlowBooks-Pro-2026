"""The register's quick add in a real browser (#386, and the #386 review):
the category picker is a plain select with an Add button beside it, so
arrowing never springs the panel; the panel makes an account and leaves out
the control accounts as parents; a payee is offered as a customer for money
in and a vendor for money out; and the question's toast is answerable by
keyboard. The API is stubbed; the page is the real one (tests/test_browser_ui.py's
fixtures)."""

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
    {
        "id": 3,
        "name": "Accounts Receivable",
        "account_number": "1100",
        "account_type": "asset",
        "is_control": True,
    },
    {
        "id": 4,
        "name": "Prepaid Expenses",
        "account_number": "1400",
        "account_type": "asset",
    },
]


def _goto_register(browser):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#qe-category")
    return page


def test_arrowing_the_category_picker_never_opens_the_panel(browser):
    page = _goto_register(browser)
    try:
        # the trap option is gone: the picker is categories only
        texts = page.locator("#qe-category option").all_inner_texts()
        assert "+ Add a new account…" not in texts
        assert not page.is_visible("#qe-category-new")

        # ArrowDown through the categories never springs the panel (the
        # review's trap: a "+ Add…" option one ArrowDown from the top).
        page.focus("#qe-category")
        page.press("#qe-category", "ArrowDown")
        assert not page.is_visible("#qe-category-new")
        page.press("#qe-category", "ArrowDown")
        assert not page.is_visible("#qe-category-new")
        # headless Chromium does not move a closed select's value on
        # ArrowDown; choosing a category (its change event) still never
        # opens the panel, and the category is reachable
        page.select_option("#qe-category", index=1)
        assert page.input_value("#qe-category") != ""
        assert not page.is_visible("#qe-category-new")

        # only the Add button opens it, with the cursor in the name
        page.click("#qe-category-add")
        page.wait_for_selector("#qe-category-new", state="visible")
        assert (
            page.evaluate("() => document.activeElement.id") == "qe-category-new-name"
        )
    finally:
        page.close()


def test_a_new_account_is_made_and_control_accounts_are_not_offered(browser):
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

        page.click("#qe-category-add")
        page.wait_for_selector("#qe-category-new", state="visible")
        page.fill("#qe-category-new-name", "Truck Fuel")
        # expense parents: the one expense account, not the bank
        assert page.locator("#qe-category-new-parent option").all_inner_texts() == [
            "None",
            "6100 - Automobile",
        ]
        # an asset: Prepaid is offered, Accounts Receivable (a control
        # account) is not, nor the bank
        page.select_option("#qe-category-new-type", "asset")
        assert page.locator("#qe-category-new-parent option").all_inner_texts() == [
            "None",
            "1400 - Prepaid Expenses",
        ]

        page.select_option("#qe-category-new-type", "expense")
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


def test_a_known_payee_is_not_offered_whatever_the_side(browser):
    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route(
            "**/api/vendors*",
            lambda r: r.fulfill(json=[{"id": 1, "name": "Acme Supply"}]),
        )
        page.route(
            "**/api/customers*",
            lambda r: r.fulfill(json=[{"id": 1, "name": "Jane Homeowner"}]),
        )
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        # an existing vendor's name, any case: no question
        page.fill("#qe-payee", "acme supply")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0

        # an existing customer's name, even on a deposit: no question
        page.fill("#qe-in", "100")
        page.fill("#qe-payee", "jane homeowner")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0
    finally:
        page.close()


def test_a_deposit_offers_a_customer_and_the_toast_answers_by_keyboard(browser):
    posted = []

    def _customers(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 9})
        return route.fulfill(json=[])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
        page.route("**/api/customers*", _customers)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        # money in: offered as a customer
        page.fill("#qe-in", "250")
        page.fill("#qe-payee", "Jane Homeowner")
        page.press("#qe-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert toast.inner_text().split() == [
            "Add",
            '"Jane',
            'Homeowner"',
            "as",
            "a",
            "customer?",
            "Add",
            "Not",
            "now",
        ]
        # the question took focus (reachable without 12 Tabs), and the row
        # kept its text
        assert page.evaluate(
            "() => document.activeElement.closest('#toast-container') !== null"
            " && document.activeElement.textContent === 'Add'"
        )
        assert page.input_value("#qe-payee") == "Jane Homeowner"

        # Not now closes it and hands focus back to the row
        toast.locator("button", has_text="Not now").click()
        assert page.locator("#toast-container .toast").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "qe-payee"
        # answered: not asked again for this name, even money out
        page.fill("#qe-in", "")
        page.fill("#qe-out", "10")
        page.fill("#qe-payee", "Jane Homeowner")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0
    finally:
        page.close()


def test_money_out_offers_a_vendor_and_add_posts_it(browser):
    posted = []

    def _vendors(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 5})
        return route.fulfill(json=[])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
        page.route("**/api/vendors*", _vendors)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        page.fill("#qe-out", "80")
        page.fill("#qe-payee", "Pine Street Lumber")
        page.press("#qe-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert "as a vendor?" in toast.inner_text()
        toast.locator("button", has_text="Add").click()
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')]"
            ".some(t => t.textContent.includes('added to Vendors'))"
        )
        assert posted == [{"name": "Pine Street Lumber"}]
    finally:
        page.close()
