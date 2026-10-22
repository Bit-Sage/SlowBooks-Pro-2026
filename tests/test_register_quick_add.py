"""The register adds a payee or a category as it is typed (#386, from a
bookkeeper's notes).

Typing a payee or a category that was not on the list meant leaving the
check, adding the vendor or the account on its own page, and coming back.
Now the entry row and the + Entry form offer to add the payee as a vendor
(Add / Not now, the entry posts either way) and open with "+ Add a new
account…" in the category picker: a panel under the picker, not a dialog,
since the form itself lives in the one dialog.

The page side is source-checked here; the browser walks the panel in
test_browser_register_quick_add.py. The server is unchanged: the vendor
route takes a name alone, and the account route a blank number.
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


def test_both_category_pickers_open_with_add_a_new_account():
    assert (
        "ADD_ACCOUNT_OPTION: '<option value=\"__new__\">+ Add a new account…</option>'"
        in BANKING_JS
    )
    for sel_id in ("qe-category", "entry-category"):
        sel = _select(sel_id)
        # the blank choice first, the add second, the chart after
        assert re.search(
            r'<option value="">[^<]*</option>\$\{BankingPage\.ADD_ACCOUNT_OPTION\}', sel
        ), sel_id
        assert "BankingPage.categoryChanged(this, ${accountId})" in sel, sel_id
        # the panel is drawn beside its picker, named for it
        assert f"BankingPage._newAccountPanel('{sel_id}', accountId)" in BANKING_JS


def test_the_add_choice_cannot_post_as_the_category():
    # the row and the form both refuse to post with the add choice still chosen
    assert (
        BANKING_JS.count(
            "=== '__new__') { toast('Add the account, or choose one', 'error'); return; }"
        )
        == 2
    )
    # the split lines keep the chart alone, without the add choice
    assert "BankingPage._splitCats = catOpts;" in BANKING_JS


def test_the_panel_asks_for_name_type_parent_and_number():
    panel = BANKING_JS[BANKING_JS.index("_newAccountPanel(selectId, accountId) {") :]
    panel = panel[: panel.index("categoryChanged(sel, accountId) {")]
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
    # the parents offered are of the chosen type
    assert "a.account_type === type && a.id !== accountId && !a.bank_kind" in BANKING_JS


def test_the_new_account_goes_into_every_picker_and_is_chosen():
    save = BANKING_JS[BANKING_JS.index("async saveNewAccount(selectId, accountId) {") :]
    assert "await API.post('/accounts', {" in save
    assert "account_number: f('number').value.trim() || null" in save
    assert "for (const id of ['qe-category', 'entry-category'])" in save
    assert "if (BankingPage._splitCats) BankingPage._splitCats += opt;" in save
    assert "document.getElementById(selectId).value = String(made.id);" in save
    assert "document.getElementById('qe-out')" in save
    assert 'input[name="description"]' in save


def test_the_payee_is_offered_as_a_vendor_on_blur_and_on_post():
    assert BANKING_JS.count('onblur="BankingPage.payeeTyped(this.value)"') == 2
    assert BANKING_JS.count("BankingPage.payeeTyped(data.payee);") == 2
    assert "API.get('/vendors?active_only=true').catch(() => null)" in BANKING_JS
    ask = BANKING_JS[BANKING_JS.index("payeeTyped(payee) {") :]
    ask = ask[: ask.index("async addVendor(name) {")]
    assert (
        "BankingPage._vendorNames.has(key) || BankingPage._payeesAsked.has(key)" in ask
    )
    assert (
        "toastAction(`Add \"${name}\" as a vendor?`, 'Add', () => BankingPage.addVendor(name), 15000, 'Not now')"
        in ask
    )
    assert "window.confirm" not in BANKING_JS
    assert "await API.post('/vendors', { name });" in BANKING_JS
    # the question's second button only closes the toast
    assert (
        "function toastAction(message, actionLabel, onClick, ms = 8000, dismissLabel = null)"
        in UTILS_JS
    )
    assert "no.addEventListener('click', () => el.remove());" in UTILS_JS


def test_a_vendor_is_made_from_its_name_alone(client):
    r = client.post("/api/vendors", json={"name": "Acme Supply"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["name"] == "Acme Supply" and v["terms"] == "Net 30" and v["is_active"]
    assert [x["name"] for x in client.get("/api/vendors?active_only=true").json()] == [
        "Acme Supply"
    ]


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
