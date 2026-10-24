"""A new chart account from the Account cell of a purchase (#485), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py.

The Add button beside a bill line's Account picker opens the panel the
register's category picker has had since #386 (now one helper,
account_quick_add.js): name, type — expense or cost of goods only on a
purchase — an optional parent and an optional number. Control accounts are
never offered as a parent. Save (or Enter in the panel) makes the account,
picks it on that line, offers it on the other lines without changing their
choice, and puts the keyboard back on the line's Account picker; the bill
is still open and posts the line there. Cancel and Escape close the panel
and give the keyboard back too. The vendor credit's lines and the expense's
account use the same panel. The open panel meets AA in both themes, and a
read-only or accountant sign-in is not offered it.

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

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
ROW1 = "#bill-lines tr:nth-child(1)"
ROW2 = "#bill-lines tr:nth-child(2)"
FOCUS = """() => { const a = document.activeElement;
    const r = a && a.closest('tr');
    const cell = a && a.closest('.cbx');
    return { cls: a ? a.className : '', row: r ? [...r.parentElement.children].indexOf(r) + 1 : 0,
        account: !!(cell && cell.querySelector('select.line-account')),
        value: a ? a.value : '' }; }"""
LINE = """(n) => { const r = document.querySelector(`#bill-lines tr:nth-child(${n})`);
    const s = r.querySelector('select.line-account');
    return { value: s.value, box: r.querySelector('.cbx--line-account .cbx-input').value,
        options: [...s.options].map(o => o.textContent), panel: !!r.querySelector('.aqa-panel') }; }"""
OPTIONS = "(sel) => [...document.querySelector(sel).options].map(o => o.textContent)"


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _bill_form(browser, company):
    page, handled = _open_at(browser, company, "#/bills")
    page.evaluate("async () => { await BillsPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    return page, handled


def test_an_account_added_from_a_bill_line_is_picked_there_and_posts(
    browser, company, books
):
    page, handled = _bill_form(browser, company)
    try:
        page.click("#bill-form button:has-text('+ Add Line')")
        page.wait_for_selector(f"{ROW2} .cbx--line-account .cbx-input")
        page.fill(f"{ROW1} .line-desc", "Diesel for the van")
        page.click(f"{ROW1} .aqa-add")
        page.wait_for_selector(f"{ROW1} .aqa-panel .aqa-name")
        opened_on = page.evaluate("() => document.activeElement.className")
        types = page.evaluate(OPTIONS, f"{ROW1} .aqa-type")
        expense_parents = page.evaluate(OPTIONS, f"{ROW1} .aqa-parent")
        page.select_option(f"{ROW1} .aqa-type", "cogs")
        cogs_parents = page.evaluate(OPTIONS, f"{ROW1} .aqa-parent")
        page.select_option(f"{ROW1} .aqa-type", "expense")
        # the open panel, both themes
        sweeps = {}
        for theme in ("dark", "light"):
            _theme(page, theme)
            sweeps[(theme, "bill line")] = page.evaluate(
                sweep_of(f"document.querySelector('{ROW1} .aqa-panel')")
            )
        parent = page.evaluate(
            f"""() => [...document.querySelector('{ROW1} .aqa-parent').options]
                .find(o => o.textContent === '6100 - Auto & Truck Expense').value"""
        )
        page.select_option(f"{ROW1} .aqa-parent", parent)
        page.fill(f"{ROW1} .aqa-name", "Truck Fuel")
        page.fill(f"{ROW1} .aqa-number", "6105")
        # Enter saves the account, not the bill
        page.press(f"{ROW1} .aqa-number", "Enter")
        page.wait_for_function(
            f"() => !document.querySelector('{ROW1} .aqa-panel')"
            f" && /^\\d+$/.test(document.querySelector('{ROW1} select.line-account').value)",
            timeout=5000,
        )
        settle(page, handled)
        still_open = page.evaluate(MODAL_SHOWN)
        focus = page.evaluate(FOCUS)
        first = page.evaluate(LINE, 1)
        second = page.evaluate(LINE, 2)
        desc = page.input_value(f"{ROW1} .line-desc")
        vendor = page.get_by_role("combobox", name="Vendor", exact=True)
        vendor.click()
        page.keyboard.type("cascade")
        page.keyboard.press("Enter")
        page.fill(f"{ROW1} .line-amt", "40")
        page.click("#bill-form button[type=submit]")
        page.wait_for_function(f"!({MODAL_SHOWN})()", timeout=5000)
        settle(page, handled)
    finally:
        page.close()

    assert opened_on == "aqa-name"
    assert types == ["Expense", "Cost of Goods Sold"]
    # control accounts are never a parent: 6000, 6120, 6150; 5000
    assert "6100 - Auto & Truck Expense" in expense_parents
    for control in (
        "6000 - Advertising & Marketing",
        "6120 - Payroll Tax Expense",
        "6150 - Employee Benefits Expense",
    ):
        assert control not in expense_parents
    assert "5100 - Materials Cost" in cogs_parents
    assert "5000 - Cost of Goods Sold" not in cogs_parents
    assert all(sweeps.values()) and below_threshold(sweeps) == []

    made = [a for a in _ok(company.get("/api/accounts")) if a["name"] == "Truck Fuel"]
    assert len(made) == 1, made
    acct = made[0]
    assert (acct["account_number"], acct["account_type"]) == ("6105", "expense")
    assert acct["parent_id"] == int(parent)

    assert still_open is True
    assert first["value"] == str(acct["id"]) and first["box"] == "6105 - Truck Fuel"
    assert first["panel"] is False and desc == "Diesel for the van"
    # back on the line's Account picker
    assert focus == {
        "cls": "cbx-input",
        "row": 1,
        "account": True,
        "value": "6105 - Truck Fuel",
    }
    # the other line can choose it, its own choice untouched
    assert second["value"] == "" and "6105 - Truck Fuel" in second["options"]

    bills = _ok(company.get("/api/bills"))
    newest = _ok(company.get(f"/api/bills/{max(b['id'] for b in bills)}"))
    posted = [ln["account_id"] for ln in newest["lines"] if float(ln["amount"])]
    assert posted == [acct["id"]]


def test_cancel_and_escape_give_the_keyboard_back_to_the_picker(
    browser, company, books
):
    page, handled = _bill_form(browser, company)
    try:
        page.click(f"{ROW1} .aqa-add")
        page.wait_for_selector(f"{ROW1} .aqa-panel .aqa-name")
        page.click(f"{ROW1} .aqa-cancel")
        settle(page, handled)
        by_click = page.evaluate(FOCUS)
        after_click = page.evaluate(LINE, 1)
        page.click(f"{ROW1} .aqa-add")
        page.wait_for_selector(f"{ROW1} .aqa-panel .aqa-name")
        page.keyboard.type("Not this one")
        page.keyboard.press("Escape")
        settle(page, handled)
        by_escape = page.evaluate(FOCUS)
        after_escape = page.evaluate(LINE, 1)
        still_open = page.evaluate(MODAL_SHOWN)
    finally:
        page.close()

    for focus in (by_click, by_escape):
        assert (focus["cls"], focus["row"], focus["account"]) == ("cbx-input", 1, True)
    for line in (after_click, after_escape):
        assert line["panel"] is False and line["value"] == ""
    assert still_open is True
    names = [a["name"] for a in _ok(company.get("/api/accounts"))]
    assert "Not this one" not in names


def test_a_vendor_credit_line_and_an_expense_add_an_account_too(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/vendor-credits")
    page.evaluate("async () => { await VendorCreditsPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    vc_row = "#vc-lines tr:nth-child(1)"
    try:
        page.click(f"{vc_row} .aqa-add")
        page.wait_for_selector(f"{vc_row} .aqa-panel .aqa-name")
        page.select_option(f"{vc_row} .aqa-type", "cogs")
        page.fill(f"{vc_row} .aqa-name", "Returned flour")
        page.click(f"{vc_row} .aqa-save")
        page.wait_for_function(
            f"() => /^\\d+$/.test(document.querySelector('{vc_row} select.line-account').value)",
            timeout=5000,
        )
        settle(page, handled)
        vc_box = page.input_value(f"{vc_row} .cbx--line-account .cbx-input")
        page.evaluate("async () => { closeModal(); await ExpensesPage.showForm(); }")
        page.wait_for_function(MODAL_SHOWN, timeout=5000)
        settle(page, handled)
        page.click("#expense-form .aqa-add")
        page.wait_for_selector("#expense-form .aqa-panel .aqa-name")
        page.fill("#expense-form .aqa-name", "Market stall fees")
        page.click("#expense-form .aqa-save")
        page.wait_for_function(
            "() => /^\\d+$/.test(document.querySelector('#expense-form [name=expense_account_id]').value)",
            timeout=5000,
        )
        settle(page, handled)
        exp_focus = page.evaluate(
            "() => { const a = document.activeElement; const c = a.closest('.cbx');"
            " return [a.className, !!(c && c.querySelector('[name=expense_account_id]')), a.value]; }"
        )
    finally:
        page.close()

    accounts = {a["name"]: a for a in _ok(company.get("/api/accounts"))}
    assert accounts["Returned flour"]["account_type"] == "cogs"
    assert vc_box == "Returned flour"
    assert accounts["Market stall fees"]["account_type"] == "expense"
    assert exp_focus == ["cbx-input", True, "Market stall fees"]


def test_a_read_only_or_accountant_sign_in_is_not_offered_add_account(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/bills")
    got = {}
    try:
        for role in ("readonly", "accountant", "admin"):
            page.evaluate(
                "async (role) => { App.role = role; closeModal();"
                " await BillsPage.showForm(); }",
                role,
            )
            page.wait_for_function(MODAL_SHOWN, timeout=5000)
            settle(page, handled)
            got[role] = page.evaluate(
                "() => [...document.querySelectorAll('#bill-lines .aqa-add')]"
                ".filter(b => !b.classList.contains('hidden')).length"
            )
        page.evaluate("() => { App.role = 'admin'; }")
    finally:
        page.close()

    assert got == {"readonly": 0, "accountant": 0, "admin": 1}
