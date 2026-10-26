"""Account numbers are optional on the chart (#567).

A bookkeeper wrote in: many businesses no longer number their accounts.
Settings → Company has a box, "Use account numbers", on by default so a
company that leaves it alone sees no change; the setup wizard asks a new
company. Off: every label, picker, report and PDF prints the name alone
instead of "6100 - Name", the chart and the Trial Balance hide the Number
column, accounts list by name within their type, and a chart import
brings a row with no number in unnumbered instead of handing it the next
free one. A number is optional on the form and the quick-add panel either
way (as since 2.23.5), an account keeps the number it has, and the seeded
chart keeps its numbers: the control accounts are still found by theirs,
so nothing posts differently.

One label helper on each side: app/services/account_label.py and
accountLabel() in app/static/js/utils.js.
"""

import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.settings import DEFAULT_SETTINGS
from app.services import control_accounts
from app.services.account_label import account_label
from app.services.accounting import create_journal_entry

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
PERIOD = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def _set(client, value):
    r = client.put("/api/settings", json={"use_account_numbers": value})
    assert r.status_code == 200, r.text


def _expense(db, name, number=None):
    acct = Account(
        name=name,
        account_type=AccountType.EXPENSE,
        account_number=number,
        balance=Decimal("0"),
    )
    db.add(acct)
    db.flush()
    return acct


def _post(db, debits, credit_to):
    lines = [
        {
            "account_id": a.id,
            "debit": Decimal("10"),
            "credit": Decimal("0"),
            "description": a.name,
        }
        for a in debits
    ]
    lines.append(
        {
            "account_id": credit_to.id,
            "debit": Decimal("0"),
            "credit": Decimal(10 * len(debits)),
            "description": "the other side",
        }
    )
    create_journal_entry(db, date(2026, 3, 1), "numbers test", lines)
    db.commit()


# ── the setting ───────────────────────────────────────────────────────────


def test_the_setting_is_on_by_default_and_takes_true_or_false(client):
    assert DEFAULT_SETTINGS["use_account_numbers"] == "true"
    assert client.get("/api/settings").json()["use_account_numbers"] == "true"
    r = client.put("/api/settings", json={"use_account_numbers": "banana"})
    assert r.status_code == 422
    assert "use_account_numbers must be one of: false, true" in r.json()["detail"]
    _set(client, "false")
    assert client.get("/api/settings").json()["use_account_numbers"] == "false"


def test_the_setup_wizard_stores_the_answer(unauthed_client):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": "hunter2hunter", "use_account_numbers": "banana"},
    )
    assert r.status_code == 422, r.text
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": "hunter2hunter", "use_account_numbers": "false"},
    )
    assert r.status_code == 200, r.text
    assert unauthed_client.get("/api/settings").json()["use_account_numbers"] == "false"


# ── the label helper ──────────────────────────────────────────────────────


def test_the_label_helper_with_and_without_numbers():
    assert account_label("6100", "Auto Expense") == "6100 - Auto Expense"
    assert account_label("6100", "Auto Expense", numbered=True) == "6100 - Auto Expense"
    assert account_label("6100", "Auto Expense", numbered=False) == "Auto Expense"
    # an account with no number reads the same either way
    assert account_label(None, "Dues") == "Dues"
    assert account_label("", "Dues", numbered=False) == "Dues"
    # a page that prints "1000 Checking" passes its own separator
    assert account_label("1000", "Checking", True, " ") == "1000 Checking"
    assert account_label("1000", "Checking", False, " ") == "Checking"
    assert account_label(None, None) == "Uncategorized"
    # the old import path still works
    from app.services.analytics import account_label as from_analytics

    assert from_analytics is account_label


# ── the form and the API ──────────────────────────────────────────────────


def test_a_blank_number_is_accepted_with_the_setting_on_or_off(client):
    # On (the default): optional since 2.23.5 (#332), and still is — a
    # company that leaves the setting alone sees no change.
    r = client.post("/api/accounts", json={"name": "Dues", "account_type": "expense"})
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] is None
    r = client.post(
        "/api/accounts",
        json={"name": "Blank", "account_type": "expense", "account_number": "  "},
    )
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] is None
    # Off: the same, and a number given is kept
    _set(client, "false")
    r = client.post("/api/accounts", json={"name": "Fees", "account_type": "expense"})
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] is None
    r = client.post(
        "/api/accounts",
        json={"name": "Legal", "account_type": "expense", "account_number": "7400"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] == "7400"
    # a number is still digits either way
    r = client.post(
        "/api/accounts",
        json={"name": "Bad", "account_type": "expense", "account_number": "ABC"},
    )
    assert r.status_code == 400
    # Back on with unnumbered accounts in the chart: allowed, they simply
    # show no number
    _set(client, "true")
    by_name = {a["name"]: a for a in client.get("/api/accounts").json()}
    assert by_name["Fees"]["account_number"] is None
    assert by_name["Legal"]["account_number"] == "7400"


# ── the order ─────────────────────────────────────────────────────────────


def test_accounts_list_by_name_within_their_type_when_off(client, db_session):
    _expense(db_session, "Zeta Utilities", "7100")
    _expense(db_session, "Bank Fees", "7900")
    _expense(db_session, "Aardvark Dues")
    _expense(db_session, "Meals")
    db_session.commit()

    def names():
        rows = client.get("/api/accounts", params={"account_type": "expense"}).json()
        return [a["name"] for a in rows]

    # on: by number, the unnumbered ones after by name
    assert names() == ["Zeta Utilities", "Bank Fees", "Aardvark Dues", "Meals"]
    _set(client, "false")
    assert names() == ["Aardvark Dues", "Bank Fees", "Meals", "Zeta Utilities"]
    _set(client, "true")
    assert names() == ["Zeta Utilities", "Bank Fees", "Aardvark Dues", "Meals"]


# ── the reports ───────────────────────────────────────────────────────────


def test_the_profit_and_loss_prints_names_alone_when_off(
    client, db_session, seed_accounts
):
    zeta = _expense(db_session, "Zeta Utilities", "7100")
    fees = _expense(db_session, "Bank Fees", "7900")
    dues = _expense(db_session, "Aardvark Dues")
    _post(db_session, [zeta, fees, dues], seed_accounts["1000"])

    def rows():
        body = client.get("/api/reports/profit-loss", params=PERIOD).json()
        flat = [(r["account_label"], r["account_number"]) for r in body["expenses"]]
        tree = [r["label"] for r in body["tree"]["expenses"]]
        return flat, tree

    flat, tree = rows()
    assert flat == [
        ("7100 - Zeta Utilities", "7100"),
        ("7900 - Bank Fees", "7900"),
        ("Aardvark Dues", None),
    ]
    assert tree == ["7100 - Zeta Utilities", "7900 - Bank Fees", "Aardvark Dues"]

    _set(client, "false")
    flat, tree = rows()
    # the name alone, listed by name; the number is still in the data
    assert flat == [
        ("Aardvark Dues", None),
        ("Bank Fees", "7900"),
        ("Zeta Utilities", "7100"),
    ]
    assert tree == ["Aardvark Dues", "Bank Fees", "Zeta Utilities"]
    # the CSV the page saves carries the same words
    csv = client.get(
        "/api/reports/profit-loss/export", params={**PERIOD, "format": "csv"}
    )
    if csv.status_code == 200:
        assert "7100 - Zeta" not in csv.text
        assert "Zeta Utilities" in csv.text
    # the trial balance and the general ledger take the same order
    tb = client.get("/api/reports/trial-balance", params=PERIOD).json()
    mine = [
        i["account_name"]
        for i in tb["items"]
        if i["account_name"] in {"Zeta Utilities", "Bank Fees", "Aardvark Dues"}
    ]
    assert mine == ["Aardvark Dues", "Bank Fees", "Zeta Utilities"]


def test_a_numbered_chart_prints_exactly_as_today_when_the_setting_is_left_on(
    client, db_session, seed_accounts
):
    zeta = _expense(db_session, "Zeta Utilities", "7100")
    fees = _expense(db_session, "Bank Fees", "7900")
    _post(db_session, [zeta, fees], seed_accounts["1000"])
    before = client.get("/api/reports/profit-loss", params=PERIOD).json()
    for r in before["expenses"]:
        assert r["account_label"] == f"{r['account_number']} - {r['account_name']}"
    _set(client, "true")  # said out loud: the same
    after = client.get("/api/reports/profit-loss", params=PERIOD).json()
    assert after == before
    tb = client.get("/api/reports/trial-balance", params=PERIOD).json()
    numbers = [i["account_number"] for i in tb["items"] if i["account_number"]]
    assert numbers == sorted(numbers)


def test_the_bank_rules_and_analytics_name_accounts_the_same_way(
    client, db_session, seed_accounts
):
    acct = _expense(db_session, "Zeta Utilities", "7100")
    _post(db_session, [acct], seed_accounts["1000"])
    r = client.post(
        "/api/bank-rules",
        json={"name": "Zeta", "pattern": "zeta", "account_id": acct.id},
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["account_name"] == "7100 - Zeta Utilities"
    _set(client, "false")
    listed = {x["id"]: x for x in client.get("/api/bank-rules").json()}
    assert listed[rule["id"]]["account_name"] == "Zeta Utilities"
    from app.services.analytics import AnalyticsEngine

    by_cat = AnalyticsEngine(db_session).expenses_by_category(
        date(2026, 1, 1), date(2026, 12, 31)
    )
    assert "Zeta Utilities" in by_cat
    assert "7100 - Zeta Utilities" not in by_cat


# ── the importer ──────────────────────────────────────────────────────────


def _upload(client, text, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(
        "/api/csv/import/accounts" + (f"?{q}" if q else ""),
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


CSV = "Number,Name,Type\n,Office Snacks,expense\n7300,Software,expense\n"


def test_the_chart_import_hands_out_a_number_only_when_the_company_uses_them(
    client, db_session, seed_accounts
):
    # on: as today, the numberless row gets the next free expense number
    plan = _upload(client, CSV, dry_run=1).json()
    rows = {r["name"]: r for r in plan["rows"]}
    assert rows["Office Snacks"]["action"] == "create"
    assert rows["Office Snacks"]["number"]
    assert rows["Software"]["number"] == "7300"
    # off: the row stays unnumbered, and a number the file gives is kept
    _set(client, "false")
    plan = _upload(client, CSV, dry_run=1).json()
    rows = {r["name"]: r for r in plan["rows"]}
    assert rows["Office Snacks"]["action"] == "create"
    assert not rows["Office Snacks"]["number"]
    assert rows["Software"]["number"] == "7300"
    r = _upload(client, CSV, dry_run=0)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    made = {a.name: a for a in db_session.query(Account).all()}
    assert made["Office Snacks"].account_number is None
    assert made["Software"].account_number == "7300"
    # a number two rows of a file both claim: the second is brought in
    # unnumbered, not renumbered (an existing account's number matches
    # that account, as always)
    twice = "Number,Name,Type\n7310,Hosting,expense\n7310,Domains,expense\n"
    plan = _upload(client, twice, dry_run=1).json()
    row = next(r for r in plan["rows"] if r["name"] == "Domains")
    assert row["action"] == "create"
    assert not row["number"]
    assert "7310 is taken; brought in unnumbered" in row["note"]
    # the seeded chart keeps its numbers
    db_session.expire_all()
    assert db_session.get(Account, seed_accounts["1100"].id).account_number == "1100"


# ── the control accounts ──────────────────────────────────────────────────


def test_the_control_accounts_are_still_found_with_numbers_off(
    client, db_session, seed_accounts
):
    _set(client, "false")
    db_session.expire_all()
    assert control_accounts.missing(db_session) == []
    for number in control_accounts.seeded_numbers():
        assert control_accounts.resolve(db_session, number) == seed_accounts[number].id
    # an unnumbered account beside them changes nothing
    client.post("/api/accounts", json={"name": "Dues", "account_type": "expense"})
    db_session.expire_all()
    assert control_accounts.resolve(db_session, "1100") == seed_accounts["1100"].id
    # the chart still marks them, and the cleanup still keeps them
    flagged = {
        a["account_number"]
        for a in client.get("/api/accounts").json()
        if a["is_control"]
    }
    assert flagged == control_accounts.seeded_numbers()
    kept = {
        k["account_number"]
        for k in client.get("/api/accounts/unused-seeded").json()["kept"]
    }
    assert control_accounts.seeded_numbers() <= kept


# ── the pages ─────────────────────────────────────────────────────────────


def test_the_pages_read_the_setting_through_one_helper():
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "function accountNumbersOn()" in utils
    assert "return s.use_account_numbers !== 'false';" in utils
    assert "const number = accountNumbersOn() ? (a.account_number || '') : '';" in utils
    assert "function accountLabel(a, name = null, sep = ' - ')" in utils
    app = (JS / "app.js").read_text(encoding="utf-8")
    # the chart hides the Number column
    assert (
        "const numbers = accountNumbersOn();\n        const columns = numbers ? 5 : 4;"
        in app
    )
    assert (
        "${numbers ? '<th scope=\"col\" style=\"width:80px;\">Number</th>' : ''}" in app
    )
    assert (
        "${numbers ? `<td style=\"font-family:var(--font-mono);\">${escapeHtml(a.account_number || '')}</td>` : ''}"
        in app
    )
    # the quick-add panel leaves the number off
    aqa = (JS / "account_quick_add.js").read_text(encoding="utf-8")
    assert "(accountNumbersOn() ? field('number'," in aqa
    assert "toast(`${accountWords(made)} added to the chart`);" in aqa
    # Settings → Company has the box; the wizard asks
    settings = (JS / "settings.js").read_text(encoding="utf-8")
    assert 'name="use_account_numbers" value="true" data-bool' in settings
    auth = (JS / "auth.js").read_text(encoding="utf-8")
    assert 'id="use_account_numbers" checked' in auth
    assert 'out.use_account_numbers = numbers.checked ? "true" : "false";' in auth
    # the trial balance hides its column too
    reports = (JS / "reports.js").read_text(encoding="utf-8")
    assert "${numbers ? '<th scope=\"col\">Number</th>' : ''}" in reports
    # no page builds "6100 - Name" by hand any more
    hand_built = re.compile(r"account_number \+ ' - '|account_number\) \+ ' - '")
    for path in sorted(JS.glob("*.js")):
        assert not hand_built.search(path.read_text(encoding="utf-8")), path.name
