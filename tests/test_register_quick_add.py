"""The register adds a payee or a category as it is typed (#386, from a
bookkeeper's notes).

Typing a payee or a category that was not on the list meant leaving the
check, adding the vendor or the account on its own page, and coming back.
Now the entry row and the + Entry form offer to add the payee — a customer
for money in, a vendor for money out — and make a new account from an Add
button beside the category picker (not an option on the native select,
which trapped the keyboard: one ArrowDown from the top opened it, #386
review). A panel under the picker, not a dialog, since the form itself
lives in the one dialog.

The page side is source-checked here; the browser walks the panel and the
keyboard in test_browser_register_quick_add.py. The server is unchanged:
the vendor and customer routes take a name alone, and the account route a
blank number.
"""

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "app/static/js"
BANKING_JS = (JS_DIR / "banking.js").read_text(encoding="utf-8")
UTILS_JS = (JS_DIR / "utils.js").read_text(encoding="utf-8")


def _select(html_id):
    m = re.search(rf'<select[^>]*id="{html_id}"[^>]*>(.*?)</select>', BANKING_JS, re.S)
    assert m, html_id
    return m.group(0)


def test_the_category_pickers_are_plain_selects_with_an_add_button():
    # the trap option is gone: arrowing the select never opens the panel
    assert "ADD_ACCOUNT_OPTION" not in BANKING_JS
    assert "__new__" not in _select("qe-category")
    assert "__new__" not in _select("entry-category")
    assert "BankingPage.categoryChanged" not in BANKING_JS
    for sel_id in ("qe-category", "entry-category"):
        # a button beside the picker opens the panel
        assert (
            f"BankingPage.openNewAccount('{sel_id}', ${{accountId}})" in BANKING_JS
        ), sel_id
        assert f"BankingPage._newAccountPanel('{sel_id}', accountId)" in BANKING_JS


def test_the_panel_asks_for_name_type_parent_and_number():
    panel = BANKING_JS[BANKING_JS.index("_newAccountPanel(selectId, accountId) {") :]
    panel = panel[: panel.index("openNewAccount(selectId, accountId) {")]
    for part in ("-name", "-type", "-parent", "-number"):
        assert f'id="${{id}}{part}"' in panel, part
    # the name is required by the script, not the attribute: a hidden
    # `required` box would block the entry form's own submit
    assert 'aria-required="true"' in panel and "required " not in panel.replace(
        "aria-required", ""
    )
    assert "BankingPage.CATEGORY_TYPES" in panel
    assert (
        "CATEGORY_TYPES: ['expense', 'income', 'cogs', 'asset', 'liability', 'equity']"
        in BANKING_JS
    )


def test_the_parent_list_leaves_out_control_accounts():
    # a control account (1100, 1200, 2000, 2200, 3200 …) is found by number
    # when a document posts, so it is never offered as a new account's parent
    assert (
        "a.account_type === type && a.id !== accountId && !a.bank_kind && !a.is_control"
        in BANKING_JS
    )


def test_the_new_account_goes_into_every_picker_and_is_chosen():
    save = BANKING_JS[BANKING_JS.index("async saveNewAccount(selectId, accountId) {") :]
    assert "await API.post('/accounts', {" in save
    assert "account_number: f('number').value.trim() || null" in save
    assert "for (const id of ['qe-category', 'entry-category'])" in save
    assert "if (BankingPage._splitCats) BankingPage._splitCats += opt;" in save
    assert "document.getElementById(selectId).value = String(made.id);" in save
    assert "document.getElementById('qe-out')" in save
    assert 'input[name="description"]' in save


def test_the_payee_is_offered_by_the_money_side_and_checks_both_lists():
    # on blur the side comes from the amount boxes, on post from the entry
    assert (
        "onblur=\"BankingPage.payeeTyped(this.value, BankingPage._rowMoneyIn(), 'qe-payee')\""
        in BANKING_JS
    )
    assert (
        "onblur=\"BankingPage.payeeTyped(this.value, BankingPage._formMoneyIn(), 'entry-payee')\""
        in BANKING_JS
    )
    assert "BankingPage.payeeTyped(data.payee, inn > 0, 'qe-payee')" in BANKING_JS
    assert (
        "BankingPage.payeeTyped(data.payee, parseFloat(data.amount) > 0, 'qe-payee')"
        in BANKING_JS
    )
    # both lists are loaded on the register and on the + Entry form
    assert "API.get('/vendors?active_only=true').catch(() => null)" in BANKING_JS
    assert "API.get('/customers?active_only=true').catch(() => null)" in BANKING_JS

    ask = BANKING_JS[BANKING_JS.index("payeeTyped(payee, moneyIn, returnFocusId) {") :]
    ask = ask[: ask.index("async addParty(name, moneyIn) {")]
    # a name already a customer OR a vendor is not offered again
    assert "BankingPage._vendorNames.has(key)" in ask
    assert "BankingPage._customerNames.has(key)" in ask
    assert "BankingPage._payeesAsked.has(key)" in ask
    # an unknown side (no amount yet) waits for the post, which knows it
    assert "if (moneyIn === null || moneyIn === undefined) return;" in ask
    # customer for money in, vendor for money out
    assert "const noun = moneyIn ? T('customer') : 'vendor';" in ask
    assert '`Add "${name}" as a ${noun}?`' in ask
    assert (
        "{ focusOnShow: true, returnFocus: () => document.getElementById(returnFocusId) }"
        in ask
    )

    assert "window.confirm" not in BANKING_JS


def test_the_party_is_made_against_the_matching_route():
    assert "const path = moneyIn ? '/customers' : '/vendors';" in BANKING_JS
    assert "await API.post(path, { name });" in BANKING_JS
    assert (
        "const names = moneyIn ? BankingPage._customerNames : BankingPage._vendorNames;"
        in BANKING_JS
    )


def test_the_question_toast_is_answerable_by_keyboard():
    assert (
        "function toastAction(message, actionLabel, onClick, ms = 8000, dismissLabel = null, opts = {})"
        in UTILS_JS
    )
    # focus moves to the action button, then back to the row it closes
    assert "if (opts.focusOnShow) focusFor(btn, true);" in UTILS_JS
    assert (
        "typeof opts.returnFocus === 'function' ? opts.returnFocus() : opts.returnFocus"
        in UTILS_JS
    )
    # the auto-dismiss holds while focused or hovered
    assert "el.matches(':hover') || el.contains(document.activeElement)" in UTILS_JS
    # the row does not yank focus back out of the toast
    assert "active.closest('#toast-container')" in BANKING_JS


def test_a_vendor_is_made_from_its_name_alone(client):
    r = client.post("/api/vendors", json={"name": "Acme Supply"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["name"] == "Acme Supply" and v["terms"] == "Net 30" and v["is_active"]
    assert [x["name"] for x in client.get("/api/vendors?active_only=true").json()] == [
        "Acme Supply"
    ]


def test_a_customer_is_made_from_its_name_alone(client):
    r = client.post("/api/customers", json={"name": "Jane Homeowner"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["name"] == "Jane Homeowner" and c["is_active"]
    assert [
        x["name"] for x in client.get("/api/customers?active_only=true").json()
    ] == ["Jane Homeowner"]


def test_an_account_is_made_with_a_blank_number_and_a_parent(client, seed_accounts):
    r = client.post(
        "/api/accounts",
        json={
            "name": "Truck Fuel",
            "account_type": "expense",
            "parent_id": None,
            "account_number": "",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] is None
    parent = r.json()["id"]
    r = client.post(
        "/api/accounts",
        json={
            "name": "Diesel",
            "account_type": "expense",
            "parent_id": parent,
            "account_number": None,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["parent_id"] == parent
    names = [a["name"] for a in client.get("/api/accounts?active_only=true").json()]
    assert "Truck Fuel" in names and "Diesel" in names
