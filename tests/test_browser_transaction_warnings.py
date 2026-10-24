"""QuickBooks Desktop's transaction warnings in playwright's Chromium
(#481), on the register's quick row and its entry form (the forms all ask
the one TxnWarn.ok; tests/test_transaction_warnings.py checks each asks):

- a date more than 90 days back or 30 ahead asks before it posts; No
  keeps the row as typed, Yes posts; inside the window, or with the
  warning off, nothing is asked;
- a duplicate check number (the server's 409) asks "Keep the number / Go
  back and edit" in a dialog of its own: Go back leaves the row with the
  caret in Ref and nothing posted, Keep sends it again with
  allow_duplicate_check_number. The question is keyboard-answerable
  (focus starts on Go back, Tab stays inside, Escape is Go back) and reads
  at WCAG AA in both themes;
- saving changes to an entry already posted asks first; off, it doesn't.

The page is the real one; the API is stubbed (tests/test_browser_ui.py).
"""

from datetime import date, timedelta

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import API, ORIGIN, _open_raw  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixture)
    _theme,
    below_threshold,
    browser_fixture,
    sweep_of,
)

ON = {
    "warn_date_past": "true",
    "warn_date_past_days": "90",
    "warn_date_future": "true",
    "warn_date_future_days": "30",
    "warn_duplicate_check_number": "true",
    "warn_edit_transaction": "true",
}
DUPLICATE = {
    "detail": {
        "code": "duplicate_check_number",
        "number": "5001",
        "message": "Check number 5001 is already used on 1000 Checking (Gas Co, 10/1/2026, $25.00).",
        "uses": [],
    }
}


def _page(browser, settings, posts, duplicate_first=False):
    page = _open_raw(browser, 1280, 800)
    answers = {"n": 0}

    def _entries(route):
        posts.append(
            (route.request.method, route.request.url, route.request.post_data_json)
        )
        answers["n"] += 1
        if duplicate_first and answers["n"] == 1:
            return route.fulfill(status=409, json=DUPLICATE)
        return route.fulfill(status=201, json={"id": 1, "note": None})

    page.route(
        "**/api/settings",
        lambda r: r.fulfill(json={**API["/api/settings"], **settings}),
    )
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
    page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
    page.route("**/api/banking/transactions*", _entries)
    page.route("**/api/banking/entries/*", _entries)
    page.goto(f"{ORIGIN}/#/banking/1")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.click("#splash-dismiss")
    page.wait_for_selector("#qe-category")
    page.wait_for_function("() => !!App.settings && 'company_name' in App.settings")
    return page


def _row(page, when, ref=""):
    page.fill("#qe-date", when)
    page.fill("#qe-payee", "Gas Co")
    page.fill("#qe-ref", ref)
    page.select_option("#qe-category", "2")
    page.fill("#qe-out", "25")


def _questions(page, answer):
    asked = []

    def _dialog(d):
        asked.append(d.message)
        d.accept() if answer else d.dismiss()

    page.on("dialog", _dialog)
    return asked


def test_a_date_far_off_asks_and_no_keeps_the_row(browser):
    posts = []
    page = _page(browser, ON, posts)
    try:
        asked = _questions(page, answer=False)
        far = (date.today() + timedelta(days=45)).isoformat()
        _row(page, far)
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_timeout(200)
        assert len(asked) == 1 and "45 days in the future" in asked[0], asked
        assert asked[0].endswith("Save it with this date?")
        assert posts == []
        assert page.input_value("#qe-date") == far  # the row as typed
    finally:
        page.close()


def test_yes_posts_and_inside_the_window_nothing_is_asked(browser):
    posts = []
    page = _page(browser, ON, posts)
    try:
        asked = _questions(page, answer=True)
        _row(page, (date.today() - timedelta(days=120)).isoformat())
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_function("() => true")
        page.wait_for_timeout(300)
        assert len(asked) == 1 and "120 days in the past" in asked[0]
        assert len(posts) == 1

        asked.clear()
        _row(page, (date.today() - timedelta(days=89)).isoformat())
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_timeout(300)
        assert asked == [] and len(posts) == 2
    finally:
        page.close()


def test_off_nothing_is_asked(browser):
    posts = []
    off = {k: ("false" if not k.endswith("_days") else v) for k, v in ON.items()}
    page = _page(browser, off, posts)
    try:
        asked = _questions(page, answer=False)
        _row(page, (date.today() + timedelta(days=700)).isoformat())
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_timeout(300)
        assert asked == [] and len(posts) == 1
    finally:
        page.close()


def test_by_default_nothing_is_asked(browser):
    """The three warnings ship off: books that never turned them on post
    a far-off date with no question."""
    posts = []
    page = _page(browser, {}, posts)
    try:
        asked = _questions(page, answer=False)
        _row(page, (date.today() + timedelta(days=700)).isoformat())
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_timeout(300)
        assert asked == [] and len(posts) == 1
    finally:
        page.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_duplicate_number_asks_keep_or_go_back(browser, theme):
    posts = []
    page = _page(browser, ON, posts, duplicate_first=True)
    try:
        _theme(page, theme)
        _row(page, date.today().isoformat(), ref="5001")
        page.focus("#qe-out")
        page.keyboard.press("Enter")
        page.wait_for_selector("[role=alertdialog]")
        box = page.locator("[role=alertdialog]")
        assert "Check number 5001 is already used" in box.inner_text()
        # named and described, focus on the safe answer, Tab stays inside
        assert (
            page.evaluate("() => document.activeElement.textContent")
            == "Go back and edit"
        )
        page.keyboard.press("Tab")
        assert (
            page.evaluate("() => document.activeElement.textContent")
            == "Keep the number"
        )
        page.keyboard.press("Tab")
        assert (
            page.evaluate("() => document.activeElement.textContent")
            == "Go back and edit"
        )
        # the gate's own sweep: every line at WCAG AA on what it is drawn on
        swept = {
            (theme, "duplicate check"): page.evaluate(
                sweep_of("document.querySelector('[role=alertdialog]')")
            )
        }
        assert len(swept[(theme, "duplicate check")]) >= 3
        assert below_threshold(swept) == []
        # Escape is Go back: the row stays, the caret in Ref, one post sent
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        assert page.locator("[role=alertdialog]").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "qe-ref"
        assert len(posts) == 1 and "allow_duplicate_check_number" not in posts[0][1]
        assert page.input_value("#qe-payee") == "Gas Co"
    finally:
        page.close()


def test_keep_the_number_sends_it_again_with_the_override(browser):
    posts = []
    page = _page(browser, ON, posts, duplicate_first=True)
    try:
        _row(page, date.today().isoformat(), ref="5001")
        page.click("#reg-quick-entry button.btn-primary")
        page.wait_for_selector("[role=alertdialog]")
        page.click("[role=alertdialog] button.btn-primary")
        page.wait_for_timeout(300)
        assert len(posts) == 2
        assert "allow_duplicate_check_number=true" in posts[1][1]
        assert posts[1][2] == posts[0][2]  # the same entry
    finally:
        page.close()


ENTRY = {
    "id": 41,
    "date": date.today().isoformat(),
    "amount": "-25.00",
    "payee": "Gas Co",
    "reference": "5001",
    "description": "",
    "category_account_id": 2,
    "splits": [],
}


@pytest.mark.parametrize("on", [True, False])
def test_saving_an_edit_asks_first_when_on(browser, on):
    posts = []
    settings = {**ON, "warn_edit_transaction": "true" if on else "false"}
    page = _page(browser, settings, posts)
    try:
        asked = _questions(page, answer=False)
        page.evaluate(
            f"() => BankingPage.showEntryForm(1, {ENTRY!r})".replace("None", "null")
        )
        page.wait_for_selector("#modal-body form button[type=submit]")
        page.wait_for_function(
            "() => document.querySelector('#modal-body form').dataset.entryId === '41'"
        )
        page.click("#modal-body form button[type=submit]")
        page.wait_for_timeout(300)
        if on:
            assert asked == [
                "You are changing a transaction that was already saved. Save your changes?"
            ]
            assert posts == []  # No: nothing saved, the form stays
            assert page.is_visible("#modal-body form")
        else:
            assert asked == [] and [m for m, _, _ in posts] == ["PUT"]
    finally:
        page.close()
