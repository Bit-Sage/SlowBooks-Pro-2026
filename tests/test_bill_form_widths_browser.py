"""The bill form's line boxes, measured in a browser (2.23.1 gate, NEW-103 /
W-5). The first fix put width rules on the selects behind the type-ahead
pickers, which the picker hides at 1px, so nothing on screen changed and a
text-only pin passed anyway: the Account box stayed 55px and "6000 -
Advertising & Marketing" read "600…". The rules sit on the visible picker
now (combobox.js mirrors a select's classes onto it as cbx--<class>) and on
the plain Description, Qty and Rate boxes. Skipped, as one module, where
playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")
from tests.test_browser_classes import classed as _classed  # noqa: E402,F401
from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)


@pytest.fixture(name="classed")
def classed_fixture(_classed):  # noqa: F811  (the fixture, by its name)
    return _classed


def test_the_bill_forms_boxes_keep_their_widths_beside_the_class_box(
    browser, company, books, classed, client
):
    # a cost code, so the form has its ninth column
    r = client.post(
        "/api/cost-codes",
        json={"code": "01-100", "name": "Site Prep", "cost_type": "labor"},
    )
    assert r.status_code in (200, 201), r.text
    page, handled = _open(browser, company)
    try:
        page.set_viewport_size({"width": 1264, "height": 821})  # the agents' window
        _visit(page, handled, "#/bills")
        page.evaluate("async () => { await BillsPage.showForm(); }")
        page.wait_for_function(OPEN, timeout=5000)
        page.wait_for_selector("#bill-lines tr .cbx--line-account")
        settle(page, handled)
        m = page.evaluate("""() => {
                const row = document.querySelector('#bill-lines tr');
                const w = (sel) => { const e = row.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().width) : null; };
                const c = row.closest('.table-container');
                return {
                    item: w('.cbx--line-item'), account: w('.cbx--line-account'), cost: w('.cbx--line-cost-code'),
                    cls: w('.line-function-fund'), desc: w('.line-desc'), qty: w('.line-qty'), rate: w('.line-rate'),
                    scrollable: c ? c.scrollWidth > c.clientWidth : null,
                    overflow: c ? getComputedStyle(c).overflowX : null,
                    hasCost: !!row.querySelector('.cbx--line-cost-code'),
                };
            }""")
        assert m["hasCost"], m  # the ninth column is there
        assert m["item"] >= 110 and m["account"] >= 200 and m["cost"] >= 110, m
        assert m["cls"] >= 124 and m["desc"] >= 140, m
        assert m["qty"] >= 64 and m["rate"] >= 84, m
        # wider than its dialog here, the table scrolls sideways rather than squeezing a box
        assert m["overflow"] == "auto", m
        if m["scrollable"]:
            scrolled = page.evaluate(
                """() => { const c = document.querySelector('#bill-lines tr').closest('.table-container');
                           c.scrollLeft = 9999; return c.scrollLeft > 0; }"""
            )
            assert scrolled, m
    finally:
        page.close()
