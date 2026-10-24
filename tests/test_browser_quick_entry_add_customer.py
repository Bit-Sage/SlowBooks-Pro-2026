"""Adding a customer from invoice Quick Entry (#484), in playwright's
Chromium on the bakery's books of tests/test_theme_contrast.py.

Quick Entry's customer picker had no way to add one; the top bar's New
Customer wiped the half-typed screen and left nobody picked. Now the picker
has the New Invoice form's "+ New Customer": a name typed that the books
don't have opens the quick add with that name in it, Save adds the
customer and picks it, the date, terms, PO and lines stay as typed, and the
focus goes back to the customer picker. Cancel does the same with no
customer. Save & Next then posts the invoice to the new customer. The open
quick add meets AA in both themes, and a read-only or accountant sign-in is
not offered it.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _theme,
    below_threshold,
    browser_fixture,
    company_fixture,
    settle,
    sweep_of,
)

NAME = "Juniper Street Diner"
STATE = """() => { const s = document.getElementById('qe-customer');
    const r = document.querySelector('#qe-lines tr');
    const box = document.getElementById('qe-new-customer-form');
    const a = document.activeElement;
    return { value: s.value, shown: s.options[s.selectedIndex].textContent,
        box: s._cbx ? s._cbx.box.value : null,
        open: !!box && box.style.display !== 'none',
        po: document.getElementById('qe-po').value,
        terms: document.getElementById('qe-terms').value,
        date: document.getElementById('qe-date').value,
        desc: r.querySelector('.line-desc').value, rate: r.querySelector('.line-rate').value,
        focus: a ? (a.id || a.className) : '',
        focusOnPicker: !!s._cbx && a === s._cbx.box,
        options: [...s.options].map(o => o.textContent) }; }"""


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _quick_entry(browser, company):
    page, handled = _open_at(browser, company, "#/quick-entry")
    page.wait_for_selector("#qe-customer + .cbx-wrap .cbx-input, .cbx-input")
    settle(page, handled)
    return page, handled


def _type_new(page, typed):
    """A name the books don't have, and Enter: "+ New Customer" is what an
    unmatched name falls to, as on the New Invoice form."""
    box = page.get_by_role("combobox", name="Customer", exact=True)
    box.click()
    page.keyboard.type(typed)
    page.wait_for_function(
        "() => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
        " return l.length === 1 && /New Customer/.test(l[0].textContent)"
        " && l[0].getAttribute('aria-selected') === 'true'; }"
    )
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('qe-new-customer-form').style.display === 'block'"
    )


def _fill_the_rest(page):
    page.select_option("#qe-terms", "Net 15")
    page.fill("#qe-date", "2026-10-02")
    page.fill("#qe-po", "PO-77")
    page.fill("#qe-lines tr .line-desc", "Breakfast pastries")
    page.fill("#qe-lines tr .line-rate", "42.5")


def test_a_customer_added_from_quick_entry_is_picked_and_nothing_typed_is_lost(
    browser, company, books
):
    page, handled = _quick_entry(browser, company)
    try:
        _fill_the_rest(page)
        _type_new(page, NAME)
        handed_on = page.input_value("#qe-new-cust-name")
        focus_in_form = page.evaluate("() => document.activeElement.id")
        page.fill("#qe-new-cust-email", "orders@juniper.example")
        # the open quick add, in both themes
        sweeps = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            sweeps[(theme, "qe-new-customer")] = page.evaluate(
                sweep_of("document.getElementById('qe-new-customer-form')")
            )
        _theme(page, "light")
        # Enter in the quick add saves the customer, not the invoice
        page.focus("#qe-new-cust-name")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => /^\\d+$/.test(document.getElementById('qe-customer').value)",
            timeout=5000,
        )
        settle(page, handled)
        after = page.evaluate(STATE)
        logged_before = page.inner_text("#qe-log")
        page.click("#qe-form button[type=submit]")
        page.wait_for_function(
            "() => document.getElementById('qe-log').textContent.includes('created')",
            timeout=5000,
        )
        settle(page, handled)
    finally:
        page.close()

    assert handed_on == NAME and focus_in_form == "qe-new-cust-name"
    texts = {it["text"] for items in sweeps.values() for it in items}
    assert "Save" in texts and "Cancel" in texts
    assert below_threshold(sweeps) == []

    made = [c for c in _ok(company.get("/api/customers")) if c["name"] == NAME]
    assert len(made) == 1, made
    cust = made[0]
    assert cust["email"] == "orders@juniper.example"
    # picked, the quick add closed, the focus back on the customer picker
    assert after["value"] == str(cust["id"]) and after["box"] == NAME
    assert after["open"] is False and after["focusOnPicker"] is True
    # everything else as typed
    assert (after["terms"], after["date"], after["po"]) == (
        "Net 15",
        "2026-10-02",
        "PO-77",
    )
    assert after["desc"] == "Breakfast pastries" and float(after["rate"]) == 42.5
    # Enter in the quick add posted no invoice
    assert "created" not in logged_before

    invoices = _ok(company.get(f"/api/invoices?customer_id={cust['id']}"))
    assert len(invoices) == 1
    inv = _ok(company.get(f"/api/invoices/{invoices[0]['id']}"))
    assert (inv["terms"], inv["date"], inv["po_number"]) == (
        "Net 15",
        "2026-10-02",
        "PO-77",
    )
    assert float(inv["total"]) == 42.5


def test_cancel_closes_the_quick_add_and_returns_to_the_customer_picker(
    browser, company, books
):
    page, handled = _quick_entry(browser, company)
    try:
        _fill_the_rest(page)
        _type_new(page, "Nobody We Know")
        page.keyboard.press("Escape")
        settle(page, handled)
        escaped = page.evaluate(STATE)
        _type_new(page, "Nobody We Know")
        page.click("#qe-new-customer-form button:has-text('Cancel')")
        settle(page, handled)
        cancelled = page.evaluate(STATE)
    finally:
        page.close()

    for got in (escaped, cancelled):
        assert got["value"] == "" and got["open"] is False
        assert got["focusOnPicker"] is True
        assert got["po"] == "PO-77" and got["desc"] == "Breakfast pastries"
    assert not [
        c for c in _ok(company.get("/api/customers")) if c["name"] == "Nobody We Know"
    ]


def test_save_and_next_adds_a_pending_customer_first(browser, company, books):
    page, handled = _quick_entry(browser, company)
    try:
        _fill_the_rest(page)
        _type_new(page, "Halsted Bakery Supply")
        page.click("#qe-form button[type=submit]")
        page.wait_for_function(
            "() => document.getElementById('qe-log').textContent.includes('created')",
            timeout=5000,
        )
        settle(page, handled)
    finally:
        page.close()

    made = [
        c
        for c in _ok(company.get("/api/customers"))
        if c["name"] == "Halsted Bakery Supply"
    ]
    assert len(made) == 1
    assert len(_ok(company.get(f"/api/invoices?customer_id={made[0]['id']}"))) == 1


def test_a_read_only_or_accountant_sign_in_is_not_offered_new_customer(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/invoices")
    got = {}
    try:
        for role in ("readonly", "accountant", "admin"):
            page.evaluate(
                "async (role) => { App.role = role;"
                " document.getElementById('page-content').innerHTML ="
                " await App.renderQuickEntry(); }",
                role,
            )
            settle(page, handled)
            got[role] = page.evaluate(
                "() => ({ options: [...document.getElementById('qe-customer').options]"
                ".map(o => o.textContent),"
                " form: !!document.getElementById('qe-new-customer-form') })"
            )
        page.evaluate("() => { App.role = 'admin'; }")
    finally:
        page.close()

    for role in ("readonly", "accountant"):
        assert not any("New Customer" in o for o in got[role]["options"]), role
        assert got[role]["form"] is False, role
    assert any("New Customer" in o for o in got["admin"]["options"])
    assert got["admin"]["form"] is True
