"""The "New lines and items are taxable" setting (#486) on the page, in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py.

Off, a new invoice's lines (the first and one added) and a new estimate's
line start with the Tax box clear, and the Items form's Taxable box starts
clear; an item that is taxable still ticks its line when picked. On (the
default), the boxes start ticked as before. Settings shows the choice as a
labelled select.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
BOXES = "(sel) => [...document.querySelectorAll(sel)].map(b => b.checked)"


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _open_form(browser, company, page_hash, call):
    page, handled = _open_at(browser, company, page_hash)
    page.evaluate(f"async () => {{ await {call}; }}")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    return page, handled


def test_off_new_sales_lines_and_items_start_untaxed(browser, company, books):
    _ok(company.put("/api/settings", json={"new_lines_taxable": "false"}))
    taxed = _ok(
        company.post(
            "/api/items",
            json={
                "name": "Taxed tin",
                "item_type": "product",
                "rate": 5,
                "is_taxable": True,
            },
        )
    )
    page, handled = _open_form(
        browser, company, "#/invoices", "InvoicesPage.showForm()"
    )
    try:
        first = page.evaluate(BOXES, "#inv-lines .line-taxable")
        page.click("#invoice-form button:has-text('+ Add Line')")
        settle(page, handled)
        both = page.evaluate(BOXES, "#inv-lines .line-taxable")
        # a taxable item still ticks the line it is picked on
        page.evaluate(
            """(id) => { const s = document.querySelector('#inv-lines tr:nth-child(2) select.line-item');
                s.value = String(id); s.dispatchEvent(new Event('change')); }""",
            taxed["id"],
        )
        settle(page, handled)
        picked = page.evaluate(BOXES, "#inv-lines .line-taxable")
    finally:
        page.close()
    page, handled = _open_form(
        browser, company, "#/estimates", "EstimatesPage.showForm()"
    )
    try:
        estimate = page.evaluate(BOXES, "#modal-body .line-taxable")
    finally:
        page.close()
    page, handled = _open_form(browser, company, "#/items", "ItemsPage.showForm()")
    try:
        item_box = page.evaluate(
            "() => document.querySelector('#modal-body [name=is_taxable]').checked"
        )
    finally:
        page.close()
    page, handled = _open_at(browser, company, "#/settings")
    try:
        setting = page.get_by_label("New lines and items are taxable", exact=True)
        chosen = setting.input_value()
    finally:
        page.close()

    assert first == [False]
    assert both == [False, False]
    assert picked == [False, True]
    assert estimate and not any(estimate)
    assert item_box is False
    assert chosen == "false"


def test_on_new_sales_lines_and_items_start_taxed(browser, company, books):
    page, handled = _open_form(
        browser, company, "#/invoices", "InvoicesPage.showForm()"
    )
    try:
        first = page.evaluate(BOXES, "#inv-lines .line-taxable")
    finally:
        page.close()
    page, handled = _open_form(browser, company, "#/items", "ItemsPage.showForm()")
    try:
        item_box = page.evaluate(
            "() => document.querySelector('#modal-body [name=is_taxable]').checked"
        )
    finally:
        page.close()
    assert first == [True]
    assert item_box is True
