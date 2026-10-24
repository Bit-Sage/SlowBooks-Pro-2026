"""The data-entry preferences (#480): "Enter key moves between fields" and
"Automatically place the decimal point", per user, both off by default.

- data_entry round-trips through the preferences store, and is unset (off)
  for a user who never chose;
- every signed-in role saves its own preferences (a read-only or an
  accountant's sign-in included: they change nothing in the books), and an
  API token still saves none;
- the hook is one class: every transaction form's money fields carry
  "money", and the fields that are not money (quantities, hours, rates in
  percent) do not;
- the register's quick row goes through the one Enter rule.

The keyboard itself is tests/test_browser_enter_moves.py.
"""

import re
from pathlib import Path

from app.models.users import ROLE_ACCOUNTANT, User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def _login(client, db, username, role):
    db.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password("long-enough-pw-1"),
            role=role,
            is_active=True,
        )
    )
    db.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": "long-enough-pw-1"},
    )
    assert r.status_code == 200, r.text


def test_the_preference_is_off_until_chosen_and_round_trips(client):
    assert client.get("/api/preferences/data_entry").json() == {
        "key": "data_entry",
        "value": None,
    }
    value = {"enter_moves": True, "auto_decimal": False}
    r = client.put("/api/preferences/data_entry", json={"value": value})
    assert r.status_code == 200
    assert client.get("/api/preferences/data_entry").json()["value"] == value


def test_every_signed_in_role_sets_its_own(client, db_session):
    for username, role in (("viewer", "readonly"), ("cpa", ROLE_ACCOUNTANT)):
        _login(client, db_session, username, role)
        # the books stay closed to the role
        assert client.post("/api/customers", json={"name": "Nope"}).status_code == 403
        r = client.put(
            "/api/preferences/data_entry", json={"value": {"enter_moves": True}}
        )
        assert r.status_code == 200, (role, r.text)
        assert client.get("/api/preferences/data_entry").json()["value"] == {
            "enter_moves": True
        }
        assert client.delete("/api/preferences/data_entry").status_code == 200


def test_a_read_only_token_still_saves_no_preference(client):
    r = client.post("/api/tokens", json={"label": "viewer-agent", "role": "readonly"})
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/auth/logout")
    r = client.put(
        "/api/preferences/data_entry",
        json={"value": {"enter_moves": True}},
        headers=headers,
    )
    assert r.status_code == 403


# The money fields of every transaction form, by file: the class hook.
MONEY = {
    "invoices.js": [
        "line-rate money",
        'name="amount" class="money"',
        "credit-take money",
    ],
    "estimates.js": ["line-rate money", "line-unit-cost money"],
    "sales_receipts.js": ["line-rate money", 'name="fair_value_amount" class="money"'],
    "credit_memos.js": ["line-rate money", "apply-amt money"],
    "bills.js": ["line-rate money", "line-amt money", "pay-amount money"],
    "vendor_credits.js": ["line-rate money", "vc-apply-amt money"],
    "purchase_orders.js": ["line-rate money"],
    "recurring.js": ["line-rate money"],
    "journal.js": ["je-debit money", "je-credit money"],
    "payments.js": [
        'name="amount" class="money"',
        "alloc-amount money",
        "credit-alloc money",
    ],
    "batch_payments.js": ["batch-amt money"],
    "expenses.js": ['name="amount" class="money"'],
    "cc_charges.js": ['name="amount" class="money"'],
    "job_costs.js": ['name="amount" class="money"'],
    "nonprofit.js": ['name="amount" class="money"'],
    "in_kind.js": ["ik-fv money"],
    "app.js": ["line-rate money"],
    "bill_quick_entry.js": ["line-amt money"],
    "banking.js": [
        'id="qe-out" class="money"',
        'id="qe-in" class="money"',
        "split-amt money",
        'name="amount" class="money"',
        'name="statement_balance" class="money"',
    ],
}


def test_every_transaction_money_field_carries_the_hook():
    missing = {
        f: [m for m in marks if m not in _js(f)]
        for f, marks in MONEY.items()
        if any(m not in _js(f) for m in marks)
    }
    assert not missing


def test_quantities_hours_and_percentages_are_not_money():
    every = "".join(_js(p.name) for p in JS.glob("*.js") if p.name != "chart.umd.js")
    for not_money in ("line-qty", "jc-qty", "ik-qty", "tax_rate", "regular_hours"):
        tags = re.findall(r"<input[^>]*%s[^>]*>" % re.escape(not_money), every)
        assert tags, not_money
        assert not [
            t for t in tags if re.search(r'class="[^"]*\bmoney\b', t)
        ], not_money


def test_the_quick_row_follows_the_one_enter_rule():
    banking = _js("banking.js")
    row = banking[banking.index("    _quickEntryRow(") :]
    row = row[: row.index("\n    },")]
    assert "BankingPage._quickKey(event, ${accountId})" in row
    assert (
        "postQuickEntry(${accountId}); }" not in row
    )  # Enter no longer posts outright
    key = banking[banking.index("    _quickKey(event, accountId) {") :]
    key = key[: key.index("\n    },")]
    assert key.index("enterMovesOn(event, row)") < key.index(
        "postQuickEntry(accountId)"
    )


def test_both_are_read_once_at_start_and_default_off():
    utils = _js("utils.js")
    assert "enter_moves: false," in utils and "auto_decimal: false," in utils
    assert "v.enter_moves === true" in utils and "v.auto_decimal === true" in utils
    assert "DataEntryPrefs.load();" in _js("app.js")
    assert "API.put('/preferences/data_entry'" in _js("settings.js")
