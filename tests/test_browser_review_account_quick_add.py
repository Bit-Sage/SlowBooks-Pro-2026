"""A new chart account from the bank-feed review and the Bank Rule form
(#492), in playwright's Chromium on the bakery's books of
tests/test_theme_contrast.py (one statement line waits in To review).

The review line's category picker and the rule's Category Account have the
Add button of the shared quick add (#485, the register's #386 panel): any
category type, control and bank accounts never a parent. Saved, the new
account is picked on that line and kept there as a pick is, every line can
choose it, and the keyboard is back on the picker; on the rule form it is
picked and the rule saves with it. The open panel meets AA in both themes,
and a read-only or accountant sign-in is not offered it.

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
OPTIONS = "(sel) => [...document.querySelector(sel).options].map(o => o.textContent)"
FOCUS = """(cls) => { const a = document.activeElement;
    const c = a && a.closest('.cbx');
    const s = c ? c.querySelector('select') : a;
    return { picker: !!(s && s.matches(cls)), value: a ? a.value : '' }; }"""


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _review(company, books):
    feed = _ok(
        company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
    )
    return [t for t in feed if t["match_status"] == "unmatched"]


def test_an_account_added_on_a_review_line_is_picked_and_kept(browser, company, books):
    waiting = _review(company, books)
    assert waiting, "the bakery's statement leaves a line to review"
    line = waiting[0]["id"]
    row = f"tr:has(#cat-{line})"
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_function(
            f"() => document.getElementById('cat-{line}').options.length > 1",
            timeout=5000,
        )
        page.click(f"{row} .aqa-add")
        page.wait_for_selector(f"{row} .aqa-panel .aqa-name")
        opened_on = page.evaluate("() => document.activeElement.className")
        types = page.evaluate(OPTIONS, f"{row} .aqa-type")
        parents = page.evaluate(OPTIONS, f"{row} .aqa-parent")
        sweeps = {}
        for theme in ("dark", "light"):
            _theme(page, theme)
            sweeps[(theme, "review")] = page.evaluate(
                sweep_of(f"document.querySelector('{row} .aqa-panel')")
            )
        page.fill(f"{row} .aqa-name", "Delivery Van Fuel")
        page.fill(f"{row} .aqa-number", "6106")
        page.click(f"{row} .aqa-save")
        page.wait_for_function(
            f"() => !document.querySelector('{row} .aqa-panel')"
            f" && /^\\d+$/.test(document.getElementById('cat-{line}').value)",
            timeout=5000,
        )
        settle(page, handled)
        focus = page.evaluate(FOCUS, f"#cat-{line}")
        shown = page.evaluate(
            f"""() => {{ const s = document.getElementById('cat-{line}');
                return [s.value, s.options[s.selectedIndex].textContent]; }}"""
        )
    finally:
        page.close()

    assert opened_on == "aqa-name"
    assert types == [
        "Expense",
        "Income",
        "Cost of Goods Sold",
        "Asset",
        "Liability",
        "Equity",
    ]
    assert "6100 - Auto & Truck Expense" in parents
    assert "6000 - Advertising & Marketing" not in parents  # a control account
    assert all(sweeps.values()) and below_threshold(sweeps) == []
    made = [
        a for a in _ok(company.get("/api/accounts")) if a["name"] == "Delivery Van Fuel"
    ]
    assert len(made) == 1 and made[0]["account_type"] == "expense"
    assert shown == [str(made[0]["id"]), "6106 - Delivery Van Fuel"]
    assert focus == {"picker": True, "value": "6106 - Delivery Van Fuel"}
    # kept on the line, as a pick is
    after = {t["id"]: t for t in _review(company, books)}
    assert after[line]["category_account_id"] == made[0]["id"]


def test_an_account_added_on_the_bank_rule_form_is_the_rules_category(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/bank-rules")
    page.evaluate("async () => { await BankRulesPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    try:
        page.fill("#modal-body [name=name]", "Market stall")
        page.fill("#modal-body [name=pattern]", "MARKET STALL")
        page.click("#modal-body .aqa-add")
        page.wait_for_selector("#modal-body .aqa-panel .aqa-name")
        types = page.evaluate(OPTIONS, "#modal-body .aqa-type")
        page.select_option("#modal-body .aqa-type", "income")
        page.fill("#modal-body .aqa-name", "Market Stall Sales")
        page.press("#modal-body .aqa-name", "Enter")
        page.wait_for_function(
            "() => !document.querySelector('#modal-body .aqa-panel')"
            " && /^\\d+$/.test(document.getElementById('rule-account').value)",
            timeout=5000,
        )
        settle(page, handled)
        still_open = page.evaluate(MODAL_SHOWN)
        focus = page.evaluate(FOCUS, "#rule-account")
        page.click("#modal-body button[type=submit]")
        page.wait_for_function(f"!({MODAL_SHOWN})()", timeout=5000)
        settle(page, handled)
    finally:
        page.close()

    assert types == ["Expense", "Income", "Asset", "Liability", "Cost of Goods Sold"]
    assert still_open is True
    assert focus == {"picker": True, "value": "Market Stall Sales"}
    made = [
        a
        for a in _ok(company.get("/api/accounts"))
        if a["name"] == "Market Stall Sales"
    ]
    assert len(made) == 1 and made[0]["account_type"] == "income"
    rules = [
        r for r in _ok(company.get("/api/bank-rules")) if r["name"] == "Market stall"
    ]
    assert len(rules) == 1 and rules[0]["account_id"] == made[0]["id"]


def test_a_read_only_or_accountant_sign_in_is_not_offered_add_account(
    browser, company, books
):
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    got = {}
    try:
        for role in ("readonly", "accountant", "admin"):
            page.evaluate(
                "async (role) => { App.role = role; closeModal(); await App.refresh(); }",
                role,
            )
            settle(page, handled)
            review = page.evaluate(
                "() => [...document.querySelectorAll('.review-cat')].map(s =>"
                " s.closest('td').querySelector('.aqa-add:not(.hidden)')).filter(Boolean).length"
            )
            page.evaluate("async () => { await BankRulesPage.showForm(); }")
            page.wait_for_function(MODAL_SHOWN, timeout=5000)
            settle(page, handled)
            rule = page.evaluate(
                "() => document.querySelectorAll('#modal-body .aqa-add:not(.hidden)').length"
            )
            got[role] = (review, rule)
        page.evaluate("() => { App.role = 'admin'; }")
    finally:
        page.close()

    assert got["readonly"] == (0, 0) and got["accountant"] == (0, 0)
    assert got["admin"][0] >= 1 and got["admin"][1] == 1
