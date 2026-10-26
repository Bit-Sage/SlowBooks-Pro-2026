"""The register's date after a post, and the date keys, in a real browser
(#566, from discussion #561). After a post the quick row and the + Entry
dialog open with the date just posted — per register, for the page's life
— and + / - step any date box a day, T is today. The #481 far-date
question is asked for a date typed, not for one carried over. The API is
stubbed; the page is the real one (tests/test_browser_ui.py's fixtures)."""

import datetime as dt

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)


def _register_page(browser, posted):
    """The Checking register (#/banking/1) with the post stubbed; `posted`
    collects the bodies sent."""

    def _transactions(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=201, json={"id": len(posted)})

    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/banking/transactions", _transactions)
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#qe-category")
    return page


def _post_quick(page, date, amount="25"):
    page.fill("#qe-date", date)
    page.select_option("#qe-category", "2")
    page.fill("#qe-out", amount)
    page.click("#reg-quick-entry button:has-text('Post')")
    page.wait_for_selector("#toast-container .toast:has-text('Entry posted')")
    # the register redraws after the post; wait for the fresh row
    page.wait_for_function("() => document.getElementById('qe-out').value === ''")


def test_the_date_sticks_after_a_post_in_the_row_and_the_dialog(browser):
    posted = []
    page = _register_page(browser, posted)
    try:
        today = dt.date.today().isoformat()
        assert page.input_value("#qe-date") == today

        _post_quick(page, "2026-10-10")
        assert posted[-1]["date"] == "2026-10-10"
        # the next row opens on the date just posted, not today
        assert page.input_value("#qe-date") == "2026-10-10"
        assert page.get_attribute("#qe-date", "data-carried") == "2026-10-10"

        # and so does + Entry
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        assert page.input_value("#modal-body [name='date']") == "2026-10-10"
        assert (
            page.get_attribute("#modal-body [name='date']", "data-carried")
            == "2026-10-10"
        )
        # a post from the dialog carries its own date back to the row
        page.fill("#modal-body [name='date']", "2026-10-12")
        page.fill("#modal-body [name='amount']", "-40")
        page.select_option("#entry-category", "2")
        page.click("#modal-body button[type='submit']")
        page.wait_for_function(
            "() => document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value === '2026-10-12'"
        )
        assert posted[-1]["date"] == "2026-10-12"

        # another register starts on today: the date is one register's
        page.goto(page.url.split("#")[0] + "#/banking/4")
        page.wait_for_function(
            "() => location.hash === '#/banking/4' && document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value !== '2026-10-12'"
        )
        assert page.input_value("#qe-date") == today
        assert page.get_attribute("#qe-date", "data-carried") == ""
        # and back: Checking still remembers its own
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_function(
            "() => location.hash === '#/banking/1' && document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value === '2026-10-12'"
        )
    finally:
        page.close()


def test_the_far_date_question_is_asked_once_not_for_the_carried_date(browser):
    posted = []
    page = _register_page(browser, posted)
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    try:
        page.evaluate(
            "() => { App.settings = Object.assign(App.settings || {},"
            " { warn_date_past: 'true', warn_date_past_days: '90' }); }"
        )
        # typed: asked, and accepted
        _post_quick(page, "2020-01-15")
        assert len(asked) == 1 and "in the past" in asked[0]
        assert posted[-1]["date"] == "2020-01-15"
        # carried from that post: not asked again
        _post_quick(page, "2020-01-15")
        assert len(asked) == 1
        assert posted[-1]["date"] == "2020-01-15"
        # moved a day with the key: a changed date, asked again
        page.focus("#qe-date")
        page.keyboard.press("+")
        assert page.input_value("#qe-date") == "2020-01-16"
        page.select_option("#qe-category", "2")
        page.fill("#qe-out", "5")
        page.click("#reg-quick-entry button:has-text('Post')")
        page.wait_for_function("() => document.getElementById('qe-out').value === ''")
        assert len(asked) == 2
        assert posted[-1]["date"] == "2020-01-16"
    finally:
        page.close()


def test_plus_minus_and_t_move_the_row_date(browser):
    page = _register_page(browser, [])
    try:
        today = dt.date.today().isoformat()
        page.fill("#qe-date", "2026-10-31")
        page.focus("#qe-date")
        page.keyboard.press("+")
        assert page.input_value("#qe-date") == "2026-11-01"
        page.keyboard.press("-")
        page.keyboard.press("-")
        assert page.input_value("#qe-date") == "2026-10-30"
        page.keyboard.press("h")
        assert page.input_value("#qe-date") == "2026-10-31"
        page.keyboard.press("m")
        assert page.input_value("#qe-date") == "2026-10-01"
        page.keyboard.press("t")
        assert page.input_value("#qe-date") == today
        # a text box keeps its + and -
        page.fill("#qe-memo", "")
        page.focus("#qe-memo")
        page.keyboard.type("a+b-c")
        assert page.input_value("#qe-memo") == "a+b-c"
        # the register's From filter is a date box too: the key moves it
        # and its onchange redraws the register
        page.fill("#reg-start", "2026-09-01")  # its change redraws the register
        page.wait_for_function(
            "() => location.hash.includes('start_date=2026-09-01')"
            " && document.getElementById('reg-start').value === '2026-09-01'"
        )
        page.focus("#reg-start")
        page.keyboard.press("-")
        page.wait_for_function(
            "() => location.hash.includes('start_date=2026-08-31')"
            " && document.getElementById('reg-start').value === '2026-08-31'"
        )
    finally:
        page.close()
