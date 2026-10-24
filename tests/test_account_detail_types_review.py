"""Account detail types (#390), the review's seven points:

1. a sub-account takes its parent's detail type, as the account API sets
   it, even when the file declared another that would fit alone (the #430
   round: a pair the API refuses was being kept); the type that gave way is
   named on the row, as is one that cannot be kept at all (chart CSV,
   migration engine), and the dry run shows each row's detail type;
2. sub-accounts of 1100 / 2000 / a bank / a card are checked on their own;
3. a bank or card account in use keeps its bank kind;
4. a base-type change a parent and its sub-account block for each other
   says how to get out (move the sub-account out first);
5. an unknown Type is a row error even beside a Detail type;
6. a Detail type of Bank / Credit Card makes a register;
7. the IIF export tolerates a stray stored detail type.
"""

import datetime as dt
import io
from types import SimpleNamespace

from app.models.accounts import Account, AccountType


def _chart(client, text, dry_run=0):
    r = client.post(
        f"/api/csv/import/accounts?dry_run={dry_run}",
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(db_session):
    db_session.expire_all()
    return {a.name: a for a in db_session.query(Account).all()}


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


# --- 1 ------------------------------------------------------------------------

NESTED = (
    "Number,Name,Type,Detail type,Parent\n"
    "8100,Vehicle loans,liability,Other Current Liability,\n"
    "8110,Truck note payable,liability,Long Term Liability,8100\n"
    "8200,Shop folder,asset,Other Current Asset,\n"
    "8210,Backhoe,asset,Fixed Asset,8200\n"
    "8300,Long-term notes,liability,Long Term Liability,\n"
    "8310,Bank note,liability,,8300\n"
    "8400,Second receivable,asset,Accounts Receivable,\n"
)


def test_a_declared_detail_type_gives_way_to_the_parents(client, db_session):
    out = _chart(client, NESTED)
    assert out["errors"] == [], out
    got = {n: a.detail_type for n, a in _by_name(db_session).items()}
    # the link stands, so the child takes the parent's type, and says so
    assert got["Truck note payable"] == "other_current_liability"
    assert got["Backhoe"] == "other_current_asset"
    notes = {r["name"]: r.get("note") or "" for r in out["rows"]}
    assert "Long Term Liability in the file" in notes["Truck note payable"]
    assert "Fixed Asset in the file" in notes["Backhoe"]
    # a row that declared none takes its parent's
    assert got["Bank note"] == "long_term_liability"


def test_a_declared_detail_type_that_cannot_be_kept_is_named(client, db_session):
    out = _chart(client, NESTED)
    row = next(r for r in out["rows"] if r["name"] == "Second receivable")
    assert "Accounts Receivable in the file; came in as Other Current Asset" in (
        row["note"]
    ), row
    assert row["detail_type"] == "other_current_asset"
    assert _by_name(db_session)["Second receivable"].detail_type == (
        "other_current_asset"
    )


def test_the_dry_run_shows_each_rows_detail_type(client, seed_accounts):
    plan = _chart(client, NESTED + "6100,Rent,expense,\n", dry_run=1)
    got = {r["name"]: r["detail_type"] for r in plan["rows"]}
    # the dry run shows the type the apply will set: the parent's (#416)
    assert got["Truck note payable"] == "other_current_liability"
    assert got["Backhoe"] == "other_current_asset"
    assert got["Rent"] == "expense"
    plan = _chart(
        client,
        "Number,Name,Type,Detail type\n2400,Loan Payable,liability,Other Current Liability\n",
        1,
    )
    (row,) = plan["rows"]
    assert row["detail_type"] == "long_term_liability"  # the chart's stays
    assert "detail type stays Long Term Liability" in row["note"]


def test_the_migration_engine_names_a_detail_type_it_could_not_keep(
    client, seed_accounts
):
    coa = (
        "Account ID,Account Description,Account Type\n"
        "10200,Regular Checking Account,Cash\n"
        "11500,Retainage Receivable,1\n"
        "40000,Sales,Income\n"
    )
    gl = (
        "Date,Reference,Jrnl,Account ID,Trans Description,Debit Amt,Credit Amt\n"
        '03/01/2026,DEP001,CRJ,10200,Deposit,"1,000.00",\n'
        '03/01/2026,DEP001,CRJ,40000,Deposit,,"1,000.00"\n'
    )
    files = [
        ("files", ("chart.csv", io.BytesIO(coa.encode()), "text/plain")),
        ("files", ("generalledger.csv", io.BytesIO(gl.encode()), "text/plain")),
    ]
    data = client.post("/api/migration/sage/import", files=files).json()
    assert data["ok"] is True, data
    assert (
        "'Retainage Receivable' is Accounts Receivable in the file and came in as "
        "Other Current Asset" in data["warnings"]
    ), data["warnings"]


def test_a_direct_build_keeps_its_own_fitting_detail_type(db_session, seed_accounts):
    folder = seed_accounts["2500"]  # Other Current Liabilities
    note = Account(
        name="Shop mortgage",
        account_type=AccountType.LIABILITY,
        detail_type="long_term_liability",
        parent_id=folder.id,
    )
    plain = Account(
        name="Accrued wages", account_type=AccountType.LIABILITY, parent_id=folder.id
    )
    db_session.add_all([note, plain])
    db_session.flush()
    assert note.detail_type == "long_term_liability"
    assert plain.detail_type == "other_current_liability"


# --- 2 ------------------------------------------------------------------------


def test_sub_accounts_of_pinned_parents_are_checked_alone(client, seed_accounts):
    retainage = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Retainage",
                "account_type": "asset",
                "parent_id": seed_accounts["1100"].id,
            },
        ),
        201,
    )
    assert retainage["detail_type"] == "other_current_asset"
    escrow = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Escrow sweep",
                "account_type": "asset",
                "parent_id": seed_accounts["1000"].id,
                "detail_type": "other_current_asset",
            },
        ),
        201,
    )
    payable = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Retainage payable",
                "account_type": "liability",
                "parent_id": seed_accounts["2000"].id,
            },
        ),
        201,
    )
    assert payable["detail_type"] == "other_current_liability"
    card_child = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Card fees accrued",
                "account_type": "liability",
                "parent_id": seed_accounts["2100"].id,
            },
        ),
        201,
    )
    # existing children are editable
    for acct in (retainage, escrow, payable, card_child):
        _ok(
            client.put(f"/api/accounts/{acct['id']}", json={"name": acct["name"] + "!"})
        )
    _ok(
        client.put(f"/api/accounts/{escrow['id']}", json={"detail_type": "fixed_asset"})
    )


# --- 3 ------------------------------------------------------------------------


def test_a_bank_account_in_use_keeps_its_bank_kind(client, db_session):
    from app.models.banking import Reconciliation

    bank = _ok(
        client.post(
            "/api/accounts",
            json={"name": "Ops", "account_type": "asset", "bank_kind": "bank"},
        ),
        201,
    )
    db_session.add(
        Reconciliation(
            account_id=bank["id"],
            statement_date=dt.date(2026, 9, 30),
            statement_balance=0,
        )
    )
    db_session.commit()
    r = client.put(
        f"/api/accounts/{bank['id']}",
        json={"detail_type": "other_current_asset", "bank_kind": None},
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith(
        "'Ops' is a bank account with 1 reconciliation(s);"
    )
    # renaming and other fields still go through
    _ok(client.put(f"/api/accounts/{bank['id']}", json={"name": "Operating"}))


def test_the_form_sends_bank_kind_only_on_a_move():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/app.js").read_text(
        encoding="utf-8"
    )
    save = js[js.index("    async saveAccount(e, id) {") :]
    save = save[: save.index("\n    },")]
    assert "data.bank_kind = null;" in save
    assert "} else if (kinds.includes(was)) {" in save
    assert "? data.detail_type : null" not in save


# --- 4 ------------------------------------------------------------------------


def test_a_base_type_change_says_how_to_get_out(client):
    folder = _ok(
        client.post(
            "/api/accounts", json={"name": "Deposits", "account_type": "liability"}
        ),
        201,
    )
    child = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Held",
                "account_type": "liability",
                "parent_id": folder["id"],
            },
        ),
        201,
    )
    r = client.put(f"/api/accounts/{folder['id']}", json={"account_type": "asset"})
    assert r.status_code == 400
    assert "Move Held out from under Deposits first" in r.json()["detail"]
    r = client.put(f"/api/accounts/{child['id']}", json={"account_type": "asset"})
    assert r.status_code == 400
    assert "Move Held out from under Deposits first" in r.json()["detail"]
    # the way out works
    _ok(client.put(f"/api/accounts/{child['id']}", json={"parent_id": None}))
    _ok(client.put(f"/api/accounts/{folder['id']}", json={"account_type": "asset"}))
    _ok(client.put(f"/api/accounts/{child['id']}", json={"account_type": "asset"}))
    moved = _ok(
        client.put(f"/api/accounts/{child['id']}", json={"parent_id": folder["id"]})
    )
    assert moved["detail_type"] == "other_current_asset"


# --- 5 and 6 ------------------------------------------------------------------


def test_an_unknown_type_is_an_error_beside_a_detail_type(client):
    out = _chart(
        client, "Number,Name,Type,Detail type\n1550,Odd,assett,Fixed Asset\n", 1
    )
    assert any("unknown account type 'assett'" in e for e in out["errors"]), out


def test_a_bank_or_card_detail_type_makes_a_register(client, db_session):
    out = _chart(
        client,
        "Number,Name,Detail type\n1060,Payroll account,Bank\n2160,Fuel card,Credit Card\n",
    )
    assert out["errors"] == [], out
    got = _by_name(db_session)
    assert (got["Payroll account"].bank_kind, got["Payroll account"].detail_type) == (
        "bank",
        "bank",
    )
    assert (got["Fuel card"].bank_kind, got["Fuel card"].detail_type) == (
        "credit_card",
        "credit_card",
    )


# --- 7 ------------------------------------------------------------------------


def test_the_iif_export_tolerates_a_stray_stored_detail_type():
    from app.services.iif_common import account_to_iif_type

    def acct(stored, atype=AccountType.ASSET, number=""):
        return SimpleNamespace(
            detail_type=stored,
            account_type=atype,
            bank_kind=None,
            account_number=number,
        )

    assert account_to_iif_type(acct("FIXED_ASSET")) == "FIXASSET"  # the NAME form
    assert account_to_iif_type(acct("garbage")) == "OCASSET"
    assert account_to_iif_type(acct(None, AccountType.LIABILITY, "2000")) == "AP"


# --- review follow-up: preview matches import, 3900 lock -----------------------


def test_the_dry_run_preview_matches_the_import_for_a_misplaced_detail(client):
    # A file that marks 1150 Accounts Receivable: AR is kept for 1100, so the
    # row comes in as Other Current Asset. The dry run must say that too, with
    # the same note, not preview as AR and then import as something else.
    csv = (
        "Number,Name,Type,Detail type\n"
        "1150,Customer Receivables,asset,Accounts Receivable\n"
    )
    preview = _chart(client, csv, dry_run=1)
    row = next(r for r in preview["rows"] if r["number"] == "1150")
    assert row["action"] == "create"
    assert row["detail_type"] == "other_current_asset"
    assert "came in as Other Current Asset" in row["note"]

    applied = _chart(client, csv, dry_run=0)
    arow = next(r for r in applied["rows"] if r["number"] == "1150")
    assert arow["detail_type"] == row["detail_type"]
    assert arow["note"] == row["note"]  # the note is not doubled on import


def test_3900_is_locked_once_it_joins_the_control_registry(client, monkeypatch):
    # #413 adds 3900 Opening Balance Equity to the control registry. The lock
    # is registry-driven, so this patches the registry to include 3900 and
    # tests the mechanism: once 3900 is a control number its type and number
    # are locked and the chart marks it control (which locks its detail type).
    from app.services import control_accounts

    registry = dict(control_accounts.CONTROL_ACCOUNTS)
    registry.setdefault(
        "3900",
        ("Opening Balance Equity", "the balancing account for opening balances"),
    )
    monkeypatch.setattr(control_accounts, "CONTROL_ACCOUNTS", registry)

    acct = _ok(
        client.post(
            "/api/accounts",
            json={
                "name": "Opening Balance Equity",
                "account_number": "3900",
                "account_type": "equity",
            },
        ),
        201,
    )
    assert acct["detail_type"] == "equity"
    assert acct["is_control"] is True

    # renumbering it is refused: the posting code finds it by number
    r = client.put(f"/api/accounts/{acct['id']}", json={"account_number": "3950"})
    assert r.status_code == 400
    assert "control account" in r.json()["detail"]

    # and changing its type away from equity is refused, which is what locks
    # its detail type (a control account's detail type cannot be contradicted)
    r = client.put(f"/api/accounts/{acct['id']}", json={"account_type": "expense"})
    assert r.status_code == 400
    assert "control account" in r.json()["detail"]
