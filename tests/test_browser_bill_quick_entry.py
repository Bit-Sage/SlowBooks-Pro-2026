"""Bill Quick Entry (#487), in playwright's Chromium on the bakery's books
of tests/test_theme_contrast.py.

A stack of vendor bills keyed one after another: the vendor (its terms
follow), the bill number, lines of an Account and an amount, Save & Next by
Ctrl+Enter. The bill posts to /api/bills with each line an amount alone; the
batch log lists it, the next bill starts with the vendor kept, the number
and lines cleared, and the keyboard on the vendor picker. A line's Account
adds a new account through the shared quick add (#485). A line with an
amount and no account is refused before anything posts. The page meets AA
in both themes; a read-only sign-in is offered neither the top bar's Bill
Entry nor the sidebar's, and finds the form locked.

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

ROW1 = "#bqe-lines tr:nth-child(1)"
ROW2 = "#bqe-lines tr:nth-child(2)"
VENDOR = "Cascade Flour Mill"


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _pick(page, box, typed, name):
    """Type into a type-ahead box and take the top match with Enter (an
    account's option also reads its group, "… Expenses")."""
    box.click()
    page.keyboard.type(typed)
    page.wait_for_function(
        "(name) => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
        " return l.length > 0 && l[0].textContent.trim().startsWith(name)"
        " && l[0].getAttribute('aria-selected') === 'true'; }",
        arg=name,
    )
    page.keyboard.press("Enter")


def _bills(company):
    return {b["id"]: b for b in _ok(company.get("/api/bills"))}


def test_a_stack_of_bills_is_keyed_one_after_another(browser, company, books):
    before = set(_bills(company))
    page, handled = _open_at(browser, company, "#/bill-entry")
    page.wait_for_selector("#bqe-form")
    settle(page, handled)
    try:
        vendor = page.get_by_role("combobox", name="Vendor", exact=True)
        _pick(page, vendor, "cascade", VENDOR)
        settle(page, handled)
        terms = page.input_value("#bqe-terms")
        page.fill("#bqe-number", "CF-1001")
        _pick(
            page,
            page.locator(f"{ROW1} .cbx--line-account .cbx-input"),
            "6400",
            "6400 - Office Supplies",
        )
        page.fill(f"{ROW1} .line-desc", "Order pads")
        page.fill(f"{ROW1} .line-amt", "120.50")
        page.click("#bqe-form button:has-text('+ Add Line')")
        page.wait_for_selector(f"{ROW2} .aqa-add")
        # a new account from the second line's Account cell
        page.click(f"{ROW2} .aqa-add")
        page.wait_for_selector(f"{ROW2} .aqa-panel .aqa-name")
        page.select_option(f"{ROW2} .aqa-type", "cogs")
        page.fill(f"{ROW2} .aqa-name", "Flour Freight")
        page.press(f"{ROW2} .aqa-name", "Enter")
        page.wait_for_function(
            f"() => /^\\d+$/.test(document.querySelector('{ROW2} select.line-account').value)",
            timeout=5000,
        )
        page.fill(f"{ROW2} .line-amt", "30")
        total = page.inner_text("#bqe-total")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector("#bqe-log tbody tr")
        settle(page, handled)
        after_one = page.evaluate(
            """() => ({ number: document.getElementById('bqe-number').value,
                lines: document.querySelectorAll('#bqe-lines tr').length,
                amt: document.querySelector('#bqe-lines .line-amt').value,
                vendor: document.getElementById('bqe-vendor').value,
                focus: document.activeElement.getAttribute('role'),
                focusName: document.activeElement.value })"""
        )
        # the second bill: an amount with no account is refused first
        page.fill("#bqe-number", "CF-1002")
        page.fill(f"{ROW1} .line-amt", "12")
        page.keyboard.press("Control+Enter")
        settle(page, handled)
        refused_rows = page.locator("#bqe-log tbody tr").count()
        refused_focus = page.evaluate(
            "() => { const c = document.activeElement.closest('.cbx');"
            " return !!(c && c.querySelector('select.line-account')); }"
        )
        page.keyboard.type("6900")
        page.keyboard.press("Enter")
        page.click("#bqe-form button[type=submit]")
        page.wait_for_function(
            "() => document.querySelectorAll('#bqe-log tbody tr').length === 2",
            timeout=5000,
        )
        settle(page, handled)
        log = page.evaluate(
            """() => ({ title: document.getElementById('bqe-log-title').textContent,
                rows: [...document.querySelectorAll('#bqe-log tbody tr')].map(r =>
                    [...r.cells].map(c => c.textContent.trim())) })"""
        )
        sweeps = {}
        for theme in ("dark", "light"):
            _theme(page, theme)
            sweeps[(theme, "bill entry")] = page.evaluate(
                sweep_of("document.getElementById('page-content')")
            )
    finally:
        page.close()

    assert terms == "Net 30"
    assert total == "Total: $150.50"
    assert after_one["number"] == "" and after_one["lines"] == 1
    assert after_one["amt"] == "" and after_one["vendor"] == str(books["vendor"])
    assert (after_one["focus"], after_one["focusName"]) == ("combobox", VENDOR)
    assert refused_rows == 1 and refused_focus is True

    made = [_bills(company)[i] for i in sorted(set(_bills(company)) - before)]
    assert [b["bill_number"] for b in made] == ["CF-1001", "CF-1002"]
    accounts = {a["id"]: a for a in _ok(company.get("/api/accounts"))}
    first = _ok(company.get(f"/api/bills/{made[0]['id']}"))
    assert first["vendor_id"] == books["vendor"] and first["terms"] == "Net 30"
    lines = sorted(first["lines"], key=lambda ln: ln["line_order"])
    assert [
        (accounts[ln["account_id"]]["name"], float(ln["amount"])) for ln in lines
    ] == [("Office Supplies", 120.5), ("Flour Freight", 30.0)]
    assert accounts[lines[1]["account_id"]]["account_type"] == "cogs"
    assert lines[0]["description"] == "Order pads"
    assert float(first["total"]) == 150.5
    second = _ok(company.get(f"/api/bills/{made[1]['id']}"))
    assert [accounts[ln["account_id"]]["account_number"] for ln in second["lines"]] == [
        "6900"
    ]

    assert log["title"] == "This batch — 2 bills, $162.50"
    assert [r[0] for r in log["rows"]] == ["CF-1002", "CF-1001"]
    assert log["rows"][1][1] == VENDOR and log["rows"][1][4] == "$150.50"
    assert all(sweeps.values()) and below_threshold(sweeps) == []


def test_a_read_only_sign_in_is_not_offered_bill_entry(browser, company, books):
    page, handled = _open_at(browser, company, "#/bills")
    settle(page, handled)
    got = {}
    try:
        # admin first: a locked sign-in's hidden top bar is not put back
        for role in ("admin", "readonly"):
            page.evaluate(
                "async (role) => { App.setRole(role); await App.navigate('#/bill-entry'); }",
                role,
            )
            settle(page, handled)
            got[role] = page.evaluate("""() => ({
                    topbar: [...document.querySelectorAll('#topbar .tb-btn[data-nav="#/bill-entry"]')]
                        .filter(b => !b.classList.contains('hidden')).length,
                    sidebar: [...document.querySelectorAll('#sidebar a[href="#/bill-entry"]')]
                        .filter(a => a.getClientRects().length).length,
                    note: document.querySelectorAll('#page-content .readonly-note').length,
                    adds: [...document.querySelectorAll('#bqe-lines .aqa-add')]
                        .filter(b => !b.classList.contains('hidden')).length })""")
        page.evaluate("() => { App.setRole('admin'); }")
    finally:
        page.close()

    assert got["readonly"] == {"topbar": 0, "sidebar": 0, "note": 1, "adds": 0}
    assert got["admin"]["topbar"] == 1 and got["admin"]["sidebar"] == 1
    assert got["admin"]["note"] == 0 and got["admin"]["adds"] == 1


# Where the keyboard is: the line cell's field class, and whether it is a
# type-ahead box.
WHERE = """() => { const a = document.activeElement;
    const c = a.closest('.cbx');
    const s = c && c.querySelector('select');
    return { cls: s ? s.className.split(' ')[0] : (a.className || '').split(' ')[0],
        combobox: a.getAttribute('role') === 'combobox',
        open: a.getAttribute('aria-expanded') === 'true' }; }"""


def test_enter_walks_a_line_past_its_type_ahead_pickers(browser, company, books):
    """With Enter-moves on, Enter on a line's Item or Account picker opened
    and closed its list and never moved on, so Enter couldn't walk a line.
    A closed picker, empty or with a name chosen, now moves on; an open one
    takes the highlighted name and stays."""
    before = set(_bills(company))
    page, handled = _open_at(browser, company, "#/bill-entry")
    page.wait_for_selector("#bqe-form")
    settle(page, handled)
    try:
        page.evaluate("() => { DataEntryPrefs.enter_moves = true; }")
        walk = []
        page.locator(f"{ROW1} .cbx--line-item .cbx-input").focus()
        walk.append(page.evaluate(WHERE))
        page.keyboard.press("Enter")  # the Item, empty and closed: on
        walk.append(page.evaluate(WHERE))
        page.keyboard.type("6400")  # the Account's list opens as it is typed
        page.wait_for_function(
            "() => { const l = document.querySelector('#cbx-listbox [aria-selected=true]');"
            " return l && l.textContent.trim().startsWith('6400'); }"
        )
        page.keyboard.press("Enter")  # open: takes the name, stays
        walk.append(page.evaluate(WHERE))
        account = page.evaluate(
            f"() => document.querySelector('{ROW1} select.line-account').value"
        )
        page.keyboard.press("Enter")  # closed, a name chosen: on
        walk.append(page.evaluate(WHERE))
        page.keyboard.press("Enter")  # the description: on
        walk.append(page.evaluate(WHERE))
        bills = set(_bills(company))
    finally:
        page.close()

    assert [(w["cls"], w["combobox"]) for w in walk] == [
        ("line-item", True),
        ("line-account", True),
        ("line-account", True),
        ("line-desc", False),
        ("line-amt", False),
    ], walk
    assert not walk[2]["open"]
    assert account and account.isdigit()
    assert bills == before  # nothing saved on the way
