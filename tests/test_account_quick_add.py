"""The new-account quick add shared by the register and the purchase forms
(#485, factored out of the register's #386 panel).

Source-checked here; the browser walks the bill line, the vendor credit
line, the expense, both themes and the locked sign-ins in
test_browser_account_quick_add.py, and the register in
test_browser_register_quick_add.py.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js"


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


AQA = _src("account_quick_add.js")


def test_it_loads_before_every_page_that_uses_it():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    at = index.index('<script src="/static/js/account_quick_add.js"></script>')
    assert index.index('<script src="/static/js/utils.js"></script>') < at
    for page in (
        "banking.js",
        "bills.js",
        "expenses.js",
        "purchase_orders.js",
        "vendor_credits.js",
    ):
        assert at < index.index(f'<script src="/static/js/{page}"></script>'), page


def test_a_purchase_offers_expense_and_cost_of_goods_only():
    assert "PURCHASE_TYPES: ['expense', 'cogs']," in AQA
    for page, profile in (
        ("bills.js", "bill"),
        ("vendor_credits.js", "vendor-credit"),
        ("purchase_orders.js", "po-convert"),
        ("expenses.js", "expense"),
    ):
        src = _src(page)
        reg = src[src.index(f"AccountQuickAdd.register('{profile}', {{") :]
        assert "types: AccountQuickAdd.PURCHASE_TYPES," in reg[:200], page
        assert f"'{profile}'" in src[: src.index("AccountQuickAdd.register(")], page
        assert "AccountQuickAdd.refillPurchase(" in reg, page


def test_a_locked_sign_in_gets_no_button_and_the_button_is_a_write_control():
    button = AQA[AQA.index("button(profile, {") :]
    button = button[: button.index("picker(selectHtml")]
    assert "if (!AccountQuickAdd.canAdd()) return '';" in button
    assert "data-write" in button
    assert "App.isLocked()" in AQA


def test_a_stock_bill_line_has_no_account_to_add():
    bills = _src("bills.js")
    assert "'bill', { disabled: stock })" in bills
    assert "if (add) add.disabled = acct.disabled;" in bills


def test_parents_leave_out_control_bank_and_inactive_accounts():
    assert (
        "a.account_type === type && a.id !== skip && !a.bank_kind && !a.is_control"
        " && a.is_active !== false" in AQA
    )


def test_the_purchase_order_conversion_offers_it_on_each_line():
    po = _src("purchase_orders.js")
    assert 'cell = AccountQuickAdd.picker(`<select class="po-conv-account"' in po
    assert "PurchaseOrdersPage._convAccounts = posting;" in po
