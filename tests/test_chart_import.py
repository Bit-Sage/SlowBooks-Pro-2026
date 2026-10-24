"""Importing a chart of accounts (#139 / #161).

The hledger files under tests/fixtures/hledger were written by hledger 1.30.1
from riverbend.journal — the importer is tested against what the tool emits,
not against a hand-typed idea of it (feedback: never ship an importer built
on synthetic data).
"""

import io
from pathlib import Path

from app.models.accounts import Account
from app.services import chart_import
from app.services.csv_export import export_accounts

FIX = Path(__file__).parent / "fixtures" / "hledger"
# Fixture password for the read-only viewer, kept out of the request literal so
# GitGuardian's username/password pair detector does not read a test as a leak.
VIEWER_PW = "long-enough-pw"


def _upload(client, text, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(
        "/api/csv/import/accounts" + (f"?{q}" if q else ""),
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


def _by_number(db):
    db.expire_all()  # the route committed on its own session; drop cached state
    return {a.account_number: a for a in db.query(Account).all() if a.account_number}


# --- hledger, the three real exports ----------------------------------------


def test_hledger_accounts_list_creates_the_chart_under_our_numbers(
    client, db_session, seed_accounts
):
    text = (FIX / "accounts.txt").read_text(encoding="utf-8")
    r = _upload(client, text, dry_run=0)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["format"] == "hledger" and out["dry_run"] is False
    assert out["errors"] == []
    db_session.expire_all()
    accts = {a.name: a for a in db_session.query(Account).all()}
    # roots are categories, not accounts
    assert "Assets" not in accts and "Revenues" not in accts and "Expenses" not in accts
    # a leaf under revenues is income, numbered in the income range
    neon = accts["Neon"]
    assert neon.account_type.value == "income" and neon.account_number.startswith("4")
    assert neon.description == "revenues:signs:neon"
    # ... and hangs from its intermediate, which the file did list
    assert neon.parent.name == "Signs" and neon.parent.description == "revenues:signs"
    # `assets:cash:petty cash` arrived with no `assets:cash` line: synthesized
    petty = accts["Petty cash"]
    assert petty.parent.name == "Cash" and petty.parent.description == "assets:cash"
    assert petty.bank_kind == "bank"
    # the file's `checking` IS our control account 1000, renamed, not a twin
    assert _by_number(db_session)["1000"].name == "Checking"
    assert sum(1 for a in accts.values() if a.name.lower() == "checking") == 1
    # `assets:receivable` is 1100 A/R renamed; `liabilities:payable` is 2000
    assert _by_number(db_session)["1100"].name == "Receivable"
    assert _by_number(db_session)["2000"].name == "Payable"
    assert _by_number(db_session)["2200"].name == "Sales tax payable"
    # `liabilities:credit card` — a parent the file never lists as a row — IS
    # control 2100, renamed to the file's spelling; the card inside it is its
    # child, a liability marked as a card
    folder = accts["Credit card"]
    assert folder.account_number == "2100"
    visa = accts["Visa"]
    assert visa.parent.account_number == "2100" and visa.bank_kind == "credit_card"
    assert visa.account_type.value == "liability" and visa.account_number.startswith(
        "2"
    )


def test_hledger_types_tag_wins_over_the_root_word(client, db_session, seed_accounts):
    text = (FIX / "accounts-types.txt").read_text(encoding="utf-8")
    out = _upload(client, text, dry_run=0).json()
    assert out["errors"] == []
    db_session.expire_all()
    accts = {a.name: a for a in db_session.query(Account).all()}
    # `; type: C` = cash, an asset you spend from -> a bank account
    assert (
        accts["Savings"].bank_kind == "bank"
        and accts["Savings"].account_type.value == "asset"
    )
    # `; type: X` -> expense; `; type: R` -> income
    assert accts["Fuel"].account_type.value == "expense"
    assert accts["Installation"].account_type.value == "income"


def test_hledger_balance_csv_is_read_as_paths_and_total_is_ignored(
    client, db_session, seed_accounts
):
    text = (FIX / "balance.csv").read_text(encoding="utf-8")
    out = _upload(client, text, dry_run=0).json()
    assert out["format"] == "hledger" and out["errors"] == []
    names = {a.name for a in db_session.query(Account).all()}
    assert "Total" not in names and "total" not in names
    assert "Truck loan" in names and "Opening balances" in names
    # no balance was imported: the column is a number hledger computed, not ours
    assert all((a.balance or 0) == 0 for a in db_session.query(Account).all())


# --- CSV in our own export's columns ----------------------------------------


def test_round_trip_of_our_export_changes_nothing(client, db_session, seed_accounts):
    text = export_accounts(db_session)
    before = {
        a.id: (a.name, a.account_number, a.account_type.value)
        for a in db_session.query(Account).all()
    }
    out = _upload(client, text, dry_run=0).json()
    assert out["format"] == "csv" and out["errors"] == []
    assert out["created"] == 0 and out["updated"] == 0 and out["skipped"] == len(before)
    after = {
        a.id: (a.name, a.account_number, a.account_type.value)
        for a in db_session.query(Account).all()
    }
    assert after == before


def test_csv_renames_by_number_creates_new_and_refuses_a_control_type_change(
    client, db_session, seed_accounts
):
    text = (
        "Number,Name,Type,Description\n"
        "1100,Trade Debtors,asset,what customers owe\n"
        "1100,Duplicate,asset,\n"
        "7100,Trade Shows,expense,booth fees\n"
        "2000,Trade Creditors,expense,\n"
        "9999,Mystery,widget,\n"
    )
    plan = _upload(client, text).json()
    assert plan["dry_run"] is True
    by_row = {r["row"]: r for r in plan["rows"]}
    assert (
        by_row[2]["action"] == "update"
        and "rename 'Accounts Receivable' → 'Trade Debtors'" in by_row[2]["changes"]
    )
    assert by_row[3]["action"] == "error" and "twice" in by_row[3]["note"]
    assert by_row[4]["action"] == "create" and by_row[4]["number"] == "7100"
    # 2000 is A/P: renamed, but the type stays liability with a reason
    assert by_row[5]["action"] == "update"
    assert (
        "type stays liability" in by_row[5]["note"] and "control" in by_row[5]["note"]
    )
    assert not any(c.startswith("type") for c in by_row[5]["changes"])
    assert any("unknown account type 'widget'" in e for e in plan["errors"])
    # nothing was written by the dry run
    assert _by_number(db_session)["1100"].name == "Accounts Receivable"
    assert "7100" not in _by_number(db_session)

    done = _upload(client, text, dry_run=0).json()
    assert done["dry_run"] is False and done["created"] == 1 and done["updated"] == 2
    nums = _by_number(db_session)
    assert (
        nums["1100"].name == "Trade Debtors"
        and nums["1100"].description == "what customers owe"
    )
    assert (
        nums["7100"].name == "Trade Shows"
        and nums["7100"].account_type.value == "expense"
    )
    assert (
        nums["2000"].name == "Trade Creditors"
        and nums["2000"].account_type.value == "liability"
    )


def test_csv_parent_by_number_and_number_inferred_type(
    client, db_session, seed_accounts
):
    text = "Number,Name,Parent\n6155,Dental plan,6150\n"
    out = _upload(client, text, dry_run=0).json()
    assert out["errors"] == [] and out["created"] == 1
    a = _by_number(db_session)["6155"]
    assert a.account_type.value == "expense" and a.parent.account_number == "6150"


def test_a_taken_number_gets_the_next_free_one_and_says_so(
    client, db_session, seed_accounts
):
    text = "Number,Name,Type\n6000,Marketing spend,expense\n6000,Trade press,expense\n"
    plan = _upload(client, text).json()
    rows = plan["rows"]
    assert rows[0]["action"] == "update"  # 6000 exists: renamed
    assert rows[1]["action"] == "error" and "twice" in rows[1]["note"]
    text = "Name,Type\nTrade press,expense\n"
    plan = _upload(client, text).json()
    assert plan["rows"][0]["number"].startswith("6") and plan["rows"][0][
        "number"
    ] not in _by_number(db_session)


def test_a_declared_child_detail_takes_the_parents_with_a_note(
    client, db_session, seed_accounts
):
    """Trent's pair (#416 / #390): the file gives the child a detail type its
    parent cannot share. The link is right, so the child takes the parent's
    and the row says so — the account API would refuse the mismatch, and the
    P&L needs the family together."""
    text = (
        "Number,Name,Type,Detail Type,Parent\n"
        "8900,Penalties,Expense,Other Expense,\n"
        "8910,Late fees,Expense,Expense,8900\n"
    )
    plan = _upload(client, text).json()
    late = next(r for r in plan["rows"] if r["name"] == "Late fees")
    assert late["detail_type"] == "other_expense"
    assert "Expense in the file" in (late["note"] or "")
    assert "Other Expense" in (late["note"] or "")

    done = _upload(client, text, dry_run=0).json()
    assert done["dry_run"] is False
    nums = _by_number(db_session)
    pen, lf = nums["8900"], nums["8910"]
    assert lf.detail_type == "other_expense" and lf.parent_id == pen.id

    got = client.get(f"/api/accounts/{lf.id}").json()
    assert got["detail_type"] == "other_expense" and got["parent_id"] == pen.id
    # a PUT resending the now-saved pair is accepted, not refused
    body = {
        k: got[k]
        for k in (
            "name",
            "account_number",
            "account_type",
            "detail_type",
            "parent_id",
            "description",
        )
    }
    assert client.put(f"/api/accounts/{lf.id}", json=body).status_code == 200


def test_a_child_under_a_pinned_parent_keeps_its_own_detail(
    client, db_session, seed_accounts
):
    """Under 1100 (or a bank) a sub-account is checked on its own detail type
    and keeps it — nothing is handed down (#416)."""
    text = (
        "Number,Name,Type,Detail Type,Parent\n"
        "1105,Retainage Receivable,asset,Other Current Asset,1100\n"
    )
    plan = _upload(client, text).json()
    row = next(r for r in plan["rows"] if r["name"] == "Retainage Receivable")
    assert row["detail_type"] == "other_current_asset"
    done = _upload(client, text, dry_run=0).json()
    assert done["dry_run"] is False
    child = _by_number(db_session)["1105"]
    assert child.detail_type == "other_current_asset"
    assert child.parent.account_number == "1100"


def test_a_grandchild_listed_before_its_parent_still_inherits(
    client, db_session, seed_accounts
):
    """Trent's order (#416): the file lists the grandchild above its parent
    above the root. The inheritance walks the tree from the roots, not the
    rows top to bottom, so Child and Grand both take the root's Other Expense
    — in the dry run and the import — and a full PUT of Grand resending its
    parent is still 200."""
    text = (
        "Number,Name,Type,Detail Type,Parent\n"
        "8920,Grand,Expense,Expense,8910\n"
        "8910,Child,Expense,Expense,8900\n"
        "8900,Root,Expense,Other Expense,\n"
    )
    plan = _upload(client, text).json()
    rows = {r["name"]: r for r in plan["rows"]}
    assert rows["Child"]["detail_type"] == "other_expense"
    assert rows["Grand"]["detail_type"] == "other_expense"
    assert "Expense in the file" in (rows["Grand"]["note"] or "")

    done = _upload(client, text, dry_run=0).json()
    assert done["dry_run"] is False
    nums = _by_number(db_session)
    root, child, grand = nums["8900"], nums["8910"], nums["8920"]
    assert child.detail_type == "other_expense" and child.parent_id == root.id
    assert grand.detail_type == "other_expense" and grand.parent_id == child.id

    got = client.get(f"/api/accounts/{grand.id}").json()
    body = {
        k: got[k]
        for k in (
            "name",
            "account_number",
            "account_type",
            "detail_type",
            "parent_id",
            "description",
        )
    }
    assert client.put(f"/api/accounts/{grand.id}", json=body).status_code == 200


def test_the_chart_import_leaves_off_an_inactive_parent(
    client, db_session, seed_accounts
):
    """An inactive account takes no sub-accounts, as the account API refuses
    one (#416); the chart import leaves the link off with the same words,
    minus the "Reactivate it" an importer cannot do, in dry run and import."""
    made = client.post(
        "/api/accounts",
        json={
            "name": "Old travel",
            "account_number": "8810",
            "account_type": "expense",
        },
    ).json()
    assert (
        client.put(f"/api/accounts/{made['id']}", json={"is_active": False}).status_code
        == 200
    )
    text = "Number,Name,Type,Parent\n8820,Tolls,Expense,8810\n"
    for dry_run in (1, 0):
        out = _upload(client, text, dry_run=dry_run).json()
        note = next(r for r in out["rows"] if r["name"] == "Tolls")["note"] or ""
        assert "8810 Old travel is inactive and cannot take sub-accounts" in note
        assert "left off" in note.lower()
        assert "Reactivate" not in note
    assert _by_number(db_session)["8820"].parent_id is None


# --- replace mode -------------------------------------------------------------


def test_replace_deactivates_unused_seeded_accounts_but_never_control_or_history(
    client, db_session, seed_accounts
):
    from decimal import Decimal
    from datetime import date
    from app.models.transactions import Transaction, TransactionLine

    # 6100 Rent carries history; 6200 does not; 1100 A/R is control
    rent = _by_number(db_session)["6100"]
    chk = _by_number(db_session)["1000"]
    txn = Transaction(date=date(2026, 1, 5), description="rent", source_type="manual")
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=rent.id,
                debit=Decimal("100"),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=chk.id,
                debit=Decimal("0"),
                credit=Decimal("100"),
            ),
        ]
    )
    db_session.commit()

    text = (FIX / "accounts.txt").read_text(encoding="utf-8")
    plan = _upload(client, text, replace=1).json()
    by_num = {r["number"]: r for r in plan["rows"] if r["number"]}
    assert by_num["6100"]["action"] == "keep" and "posted" in by_num["6100"]["note"]
    assert by_num["1200"]["action"] == "keep" and "control" in by_num["1200"]["note"]
    assert by_num["6200"]["action"] == "deactivate"
    assert plan["deactivated"] > 10

    done = _upload(client, text, replace=1, dry_run=0).json()
    assert done["deactivated"] == plan["deactivated"]
    nums = _by_number(db_session)
    assert nums["6200"].is_active is False
    assert nums["6100"].is_active is True and nums["1200"].is_active is True
    assert all(
        nums[n].is_active
        for n in ("1000", "1100", "2000", "2100", "2200", "3200", "4000", "5000")
    )


# --- edges ---------------------------------------------------------------------


def test_empty_and_headerless_files_are_refused_plainly(client, seed_accounts):
    r = _upload(client, "   \n")
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    plan = _upload(client, "Balance,Notes\n12,x\n").json()
    assert (
        any("no account name column" in e for e in plan["errors"])
        and plan["rows"] == []
    )


def test_readonly_cannot_import(client, seed_accounts):
    # the role policy is by method: a readonly session is GET-only
    from app.models.users import ROLE_READONLY

    r = client.post(
        "/api/users",
        json={
            "username": "viewer",
            "password": VIEWER_PW,
            "role": ROLE_READONLY,
        },
    )
    assert r.status_code == 201, r.text
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "viewer", "password": VIEWER_PW}
    )
    assert r.status_code == 200, r.text
    r = _upload(client, "Name,Type\nX,expense\n")
    assert r.status_code == 403


def test_service_parses_the_journal_declarations_too():
    # `account assets:bank:checking ; type: C` lines straight out of a journal
    text = "account assets:bank:checking   ; type: C\naccount expenses:rent ; type: X\n; a comment\n"
    rows, errors, fmt = chart_import.parse_chart(text)
    assert fmt == "hledger" and errors == []
    assert [(r.name, r.type, r.bank_kind) for r in rows] == [
        ("Bank", "asset", "bank"),
        ("Checking", "asset", "bank"),
        ("Rent", "expense", None),
    ]


# --- the 2.15.0 gate's finding (skytech) --------------------------------------


def test_a_parent_segment_named_like_a_control_account_becomes_it_and_the_import_is_idempotent(
    client, db_session, seed_accounts
):
    """`assets:inventory` is never a row in `hledger accounts` output, only a
    parent. It was being created as a second, active 'Inventory' beside control
    1300 — and again on every re-import. The parent IS 1300; the tree hangs
    from the account the ledger posts to."""
    from app.services.control_accounts import CONTROL_ACCOUNTS

    text = (FIX / "accounts.txt").read_text(encoding="utf-8")
    plan = _upload(client, text).json()
    control_names = {entry[0].lower() for entry in CONTROL_ACCOUNTS.values()}
    twins = [
        r
        for r in plan["rows"]
        if r["action"] == "create" and r["name"].lower() in control_names
    ]
    assert twins == [], twins
    by_name = {r["name"]: r for r in plan["rows"]}
    assert (
        by_name["Inventory"]["action"] in ("update", "skip")
        and by_name["Inventory"]["number"] == "1300"
    )
    assert (
        by_name["Credit card"]["action"] in ("update", "skip")
        and by_name["Credit card"]["number"] == "2100"
    )

    done = _upload(client, text, dry_run=0).json()
    # 30 before the fix (two twins); now the two parents take 1300 and 2100,
    # and Visa becomes a child card instead of taking 2100 itself
    assert done["created"] == 29, done
    nums = _by_number(db_session)
    names = {a.name: a for a in db_session.query(Account).all()}
    assert names["Neon tubes"].parent.account_number == "1300"
    assert (
        names["Visa"].parent.account_number == "2100"
        and names["Visa"].bank_kind == "credit_card"
    )
    active = [a.name for a in names.values() if a.is_active]
    assert len(active) == len(set(active)), "duplicate active names"

    # the same file again: nothing to do
    again = _upload(client, text).json()
    assert again["created"] == 0 and again["updated"] == 0, {
        r["name"]: r for r in again["rows"] if r["action"] != "skip"
    }
    assert nums["1300"].name == "Inventory"


def test_the_shipped_template_imports_cleanly_into_the_seeded_chart(
    client, db_session, seed_accounts
):
    """The template the dialog offers must go through the importer it is a
    template for: every row lands (create or update), no errors, and its
    parents resolve. Also pins that the file the site hands out is the
    same one the app serves."""
    text = (
        Path(__file__).parents[1]
        / "app/static/downloads/chart-of-accounts-template.csv"
    ).read_text(encoding="utf-8")
    plan = _upload(client, text).json()
    assert plan["format"] == "csv" and plan["errors"] == []
    assert not any(r["action"] == "error" for r in plan["rows"])
    assert not any("not found" in (r.get("note") or "") for r in plan["rows"]), plan[
        "rows"
    ]
    r = client.get("/static/downloads/chart-of-accounts-template.csv")
    assert r.status_code == 200 and r.text == text


def test_the_control_registry_is_sixteen_accounts():
    """The gate's checks compare created rows against "the fifteen control
    names". One entry is declared across two lines, and a line-based reader
    returned fourteen without noticing (skytech, 2.15.0 gate). Pin the count
    where a change to the registry will trip it. Sixteen since 3900 Opening
    Balance Equity is found by number (#413)."""
    from app.services.control_accounts import CONTROL_ACCOUNTS

    assert len(CONTROL_ACCOUNTS) == 16
    assert "3900" in CONTROL_ACCOUNTS
    assert "3200" in CONTROL_ACCOUNTS


def test_csv_import_refuses_a_foreign_account_on_a_reserved_number(
    client, db_session, seed_accounts
):
    """The review's hole: `/api/csv/import/accounts` had no reserved-number
    guard, so a `5900,Marketing Promotions,expense` row created 5900 under a
    foreign name and every inventory write-off then posted there. The new-row
    path now refuses it, the dry run shows the refusal, and apply creates
    nothing. 5900 is on demand, so the slot is empty on a seeded chart."""
    text = "Number,Name,Type\n5900,Marketing Promotions,expense\n"

    out = _upload(client, text, dry_run=1).json()
    row = next(x for x in out["rows"] if x["name"] == "Marketing Promotions")
    assert row["action"] == "error"
    assert "5900" in row["note"] and "reserved" in row["note"].lower()
    assert out["row_errors"] >= 1
    assert "5900" not in _by_number(db_session)

    out = _upload(client, text, dry_run=0).json()
    assert out["dry_run"] is False
    assert "5900" not in _by_number(db_session)


def test_csv_import_restores_the_control_account_itself(
    client, db_session, seed_accounts
):
    """The guard refuses a FOREIGN account only: a row that IS the control
    account — the registry's name and expected type — restores the empty
    slot, the same exception the API's New Account form allows."""
    text = "Number,Name,Type\n5900,Inventory Adjustments,expense\n"
    out = _upload(client, text, dry_run=0).json()
    assert out["row_errors"] == 0
    created = _by_number(db_session)
    assert "5900" in created
    assert created["5900"].name == "Inventory Adjustments"
    assert created["5900"].account_type.value == "expense"
