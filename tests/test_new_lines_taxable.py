"""A company setting so new lines and items are not taxable by default (#486).

Every new invoice, estimate, sales receipt, credit memo and recurring line
started with its Tax box ticked and every new item was taxable; a service
company that charges no sales tax unticked the box on every line. Settings
-> Invoice Defaults now has "New lines and items are taxable" (on, as
before). Off, a line or an item that does not say is_taxable is untaxed:
the API's default, the sales forms' new-line box, the Items form and the
item quick add. A line that says, an item's own flag and a tax-exempt
customer keep working either way.
"""

from decimal import Decimal
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "app/static/js"


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _off(client):
    _ok(client.put("/api/settings", json={"new_lines_taxable": "false"}))


def _item(client, name, **extra):
    return _ok(
        client.post(
            "/api/items",
            json={"name": name, "item_type": "service", "rate": 50, **extra},
        )
    )


def _invoice(client, customer_id, lines):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-10-01",
                "tax_rate": 0.08,
                "lines": lines,
            },
        )
    )


def test_the_setting_is_on_by_default_and_takes_only_true_or_false(client):
    assert _ok(client.get("/api/settings"))["new_lines_taxable"] == "true"
    r = client.put("/api/settings", json={"new_lines_taxable": "maybe"})
    assert r.status_code in (400, 422), r.text
    _off(client)
    assert _ok(client.get("/api/settings"))["new_lines_taxable"] == "false"


def test_on_an_unsaid_line_and_item_are_taxable_as_before(
    client, seed_accounts, seed_customer
):
    item = _item(client, "Consult")
    assert item["is_taxable"] is True
    inv = _invoice(
        client,
        seed_customer.id,
        [{"description": "Labor", "quantity": 1, "rate": 100}],
    )
    assert [ln["is_taxable"] for ln in inv["lines"]] == [True]
    assert Decimal(str(inv["tax_amount"])) == Decimal("8.00")


def test_off_an_unsaid_line_and_item_are_untaxed(client, seed_accounts, seed_customer):
    _off(client)
    item = _item(client, "Consult")
    assert item["is_taxable"] is False
    taxed = _item(client, "Part", is_taxable=True)
    assert taxed["is_taxable"] is True
    inv = _invoice(
        client,
        seed_customer.id,
        [
            {"description": "Labor", "quantity": 1, "rate": 100},
            {
                "description": "Taxed by hand",
                "quantity": 1,
                "rate": 10,
                "is_taxable": True,
            },
            # the item's own flag still decides an unsaid line on it
            {"item_id": taxed["id"], "description": "Part", "quantity": 1, "rate": 50},
            {
                "item_id": item["id"],
                "description": "Consult",
                "quantity": 1,
                "rate": 50,
            },
        ],
    )
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False, True, True, False]
    assert Decimal(str(inv["tax_amount"])) == Decimal("4.80")


def test_off_reaches_estimates_credit_memos_and_recurring(
    client, seed_accounts, seed_customer
):
    _off(client)
    line = [{"description": "Labor", "quantity": 1, "rate": 100}]
    est = _ok(
        client.post(
            "/api/estimates",
            json={"customer_id": seed_customer.id, "date": "2026-10-01", "lines": line},
        )
    )
    assert [ln["is_taxable"] for ln in est["lines"]] == [False]
    cm = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-10-01",
                "tax_rate": 0.08,
                "lines": line,
            },
        )
    )
    assert Decimal(str(cm["tax_amount"])) == Decimal("0.00")
    rec = _ok(
        client.post(
            "/api/recurring",
            json={
                "customer_id": seed_customer.id,
                "start_date": "2026-11-01",
                "frequency": "monthly",
                "lines": line,
            },
        )
    )
    assert [ln["is_taxable"] for ln in rec["lines"]] == [False]


def test_a_tax_exempt_customer_is_untaxed_with_the_setting_on(client, seed_accounts):
    cust = _ok(
        client.post("/api/customers", json={"name": "Reseller", "is_taxable": False})
    )
    inv = _invoice(
        client,
        cust["id"],
        [{"description": "Goods", "quantity": 1, "rate": 100, "is_taxable": True}],
    )
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False]


@pytest.mark.parametrize(
    "page", ["invoices.js", "estimates.js", "recurring.js", "sales_receipts.js"]
)
def test_the_sales_forms_new_line_box_follows_the_setting(page):
    src = (JS_DIR / page).read_text(encoding="utf-8")
    assert "${TaxDefault.checked(line)}" in src
    assert "line.is_taxable === false ? '' : 'checked'" not in src


def test_the_credit_memo_items_form_and_settings_page_follow_it():
    cm = (JS_DIR / "credit_memos.js").read_text(encoding="utf-8")
    assert "${TaxDefault.checked()}" in cm
    items = (JS_DIR / "items.js").read_text(encoding="utf-8")
    assert "is_taxable: TaxDefault.on()," in items
    invoices = (JS_DIR / "invoices.js").read_text(encoding="utf-8")
    assert "return s.new_lines_taxable !== 'false';" in invoices
    settings = (JS_DIR / "settings.js").read_text(encoding="utf-8")
    assert (
        '<label for="new-lines-taxable">New lines and items are taxable</label>'
        in settings
    )
    assert (
        'name="new_lines_taxable" aria-describedby="new-lines-taxable-help"' in settings
    )
