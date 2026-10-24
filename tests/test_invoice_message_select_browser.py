"""The Message select on the invoice form drives Notes (#392, #407 review).

Choosing a saved message copies its text into Notes; but when Notes hold
something typed by hand the chosen text is appended on a new line rather
than replacing it, so nothing is thrown away. "(none)" clears Notes only
when they still hold a saved message's text.

Reuses the contrast sweep's harness (tests/test_theme_contrast.py) and a
company with saved messages. Skipped, as one module, where playwright or
its Chromium is not installed.
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

DEFAULT_BODY = "Thank you for your business."
ZELLE_BODY = "To pay by Zelle use pay@example.com."
HAND = "Deliver to back gate, code 4471"


@pytest.fixture(name="messages")
def messages_fixture(client, books):
    """A saved default message and one more, so the Message select renders."""
    default = client.post(
        "/api/invoice-messages",
        json={"name": "Thanks", "body": DEFAULT_BODY, "is_default": True},
    )
    assert default.status_code == 201, default.text
    zelle = client.post(
        "/api/invoice-messages", json={"name": "Zelle", "body": ZELLE_BODY}
    )
    assert zelle.status_code == 201, zelle.text
    return {"default": default.json(), "zelle": zelle.json()}


def _new_form(browser, company):
    """A new-invoice form, open, with the Message select rendered."""
    page, handled = _open(browser, company)
    _visit(page, handled, "#/invoices")
    page.evaluate("async () => { await InvoicesPage.showForm(); }")
    page.wait_for_function(OPEN, timeout=5000)
    settle(page, handled)
    page.wait_for_selector("#inv-message-select", timeout=5000)
    return page, handled


def _notes(page):
    return page.evaluate(
        "() => document.querySelector('#invoice-form [name=notes]').value"
    )


def _set_notes(page, text):
    page.fill("#invoice-form [name=notes]", text)


def _zelle_value(messages):
    return str(messages["zelle"]["id"])


def test_a_new_form_starts_with_the_default_message(browser, company, messages):
    page, _ = _new_form(browser, company)
    try:
        assert _notes(page) == DEFAULT_BODY
    finally:
        page.close()


def test_choosing_a_message_replaces_a_saved_bodys_text(browser, company, messages):
    # Notes hold the default message's text as it is, so picking another
    # message replaces it rather than stacking the two.
    page, _ = _new_form(browser, company)
    try:
        page.select_option("#inv-message-select", _zelle_value(messages))
        assert _notes(page) == ZELLE_BODY
    finally:
        page.close()


def test_choosing_a_message_appends_to_hand_typed_notes(browser, company, messages):
    # The #407 blocker: typed text must survive — the chosen body is added
    # on a new line instead of overwriting it.
    page, _ = _new_form(browser, company)
    try:
        _set_notes(page, HAND)
        page.select_option("#inv-message-select", _zelle_value(messages))
        assert _notes(page) == f"{HAND}\n{ZELLE_BODY}"
    finally:
        page.close()


def test_none_keeps_hand_typed_notes_but_clears_a_saved_body(
    browser, company, messages
):
    page, _ = _new_form(browser, company)
    try:
        # (none) over hand-typed text leaves it alone
        _set_notes(page, HAND)
        page.select_option("#inv-message-select", "")
        assert _notes(page) == HAND
        # (none) over a saved message's text clears it
        _set_notes(page, ZELLE_BODY)
        page.select_option("#inv-message-select", "")
        assert _notes(page) == ""
    finally:
        page.close()
