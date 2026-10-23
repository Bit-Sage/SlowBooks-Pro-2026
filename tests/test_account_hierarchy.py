"""Sub-accounts set and cleared from the account form (#416, the chart half).

The form's parent picker sends `parent_id` on a PUT; the server's rules are
the ones #390 (detail types) and Testing #101 (parent loops) put there, plus
one more: under 1100, 2000, a bank or a card a sub-account keeps its own
detail type but not another base type.
"""

from app.models.accounts import Account, AccountType


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _new(client, name, atype="expense", **extra):
    return _ok(
        client.post(
            "/api/accounts", json={"name": name, "account_type": atype, **extra}
        ),
        201,
    )


def test_a_parent_is_set_and_cleared_with_put(client, seed_accounts):
    travel = _new(client, "Travel")
    hotel = _new(client, "Hotel")
    moved = _ok(
        client.put(f"/api/accounts/{hotel['id']}", json={"parent_id": travel["id"]})
    )
    assert moved["parent_id"] == travel["id"]
    assert _ok(client.get(f"/api/accounts/{hotel['id']}"))["parent_id"] == travel["id"]
    cleared = _ok(client.put(f"/api/accounts/{hotel['id']}", json={"parent_id": None}))
    assert cleared["parent_id"] is None


def test_a_parent_under_its_own_sub_account_is_refused(client, seed_accounts):
    travel = _new(client, "Travel")
    hotel = _new(client, "Hotel", parent_id=travel["id"])
    r = client.put(f"/api/accounts/{travel['id']}", json={"parent_id": hotel["id"]})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "That would make Travel a sub-account of itself (through Hotel)."
    )


def test_a_parent_of_another_type_is_refused(client, seed_accounts):
    sales = _new(client, "Sales", "income")
    hotel = _new(client, "Hotel")
    r = client.put(f"/api/accounts/{hotel['id']}", json={"parent_id": sales["id"]})
    assert r.status_code == 400
    assert r.json()["detail"].startswith(
        "a sub-account shares its parent's detail type, except under a bank, "
        "card, Accounts Receivable or Accounts Payable parent; a sub-account "
        "of Sales (Income) must be"
    )


def test_a_sub_account_takes_its_parents_detail_type(client, seed_accounts):
    other = _new(client, "Penalties", detail_type="other_expense")
    fine = _new(client, "Parking fine")
    moved = _ok(
        client.put(f"/api/accounts/{fine['id']}", json={"parent_id": other["id"]})
    )
    assert moved["detail_type"] == "other_expense"
    # the form sends the detail type it shows; a different one is refused
    r = client.put(
        f"/api/accounts/{fine['id']}",
        json={"detail_type": "expense", "account_type": "expense"},
    )
    assert r.status_code == 400
    assert "must be an Other Expense too" in r.json()["detail"]


def test_pinned_parents_take_their_own_base_type_only(client, seed_accounts):
    bank = seed_accounts["1000"]
    ar = seed_accounts["1100"]
    # any asset detail type under a bank or 1100
    sweep = _new(client, "Sweep", "asset", parent_id=bank.id)
    assert sweep["detail_type"] == "other_current_asset"
    retainage = _new(client, "Retainage", "asset", detail_type="fixed_asset")
    _ok(client.put(f"/api/accounts/{retainage['id']}", json={"parent_id": ar.id}))
    # but not an expense: refused on create and on a move
    r = client.post(
        "/api/accounts",
        json={"name": "Bank fees", "account_type": "expense", "parent_id": bank.id},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"a sub-account of {bank.account_number} {bank.name} (Bank) must be "
        "an asset too"
    )
    fees = _new(client, "Bank fees")
    r = client.put(f"/api/accounts/{fees['id']}", json={"parent_id": bank.id})
    assert r.status_code == 400
    assert "must be an asset too" in r.json()["detail"]


def test_a_type_change_under_a_pinned_parent_says_how_to_get_out(client, seed_accounts):
    bank = seed_accounts["1000"]
    sweep = _new(client, "Sweep", "asset", parent_id=bank.id)
    r = client.put(
        f"/api/accounts/{sweep['id']}",
        json={"account_type": "expense", "detail_type": "expense"},
    )
    assert r.status_code == 400
    assert r.json()["detail"].endswith(
        f"Move Sweep out from under {bank.account_number} {bank.name} first "
        "(Parent account: none, on its form), change the types, then put it back."
    )
    # the form's way out: no parent and the new type in one save
    out = _ok(
        client.put(
            f"/api/accounts/{sweep['id']}",
            json={
                "parent_id": None,
                "account_type": "expense",
                "detail_type": "expense",
            },
        )
    )
    assert (out["parent_id"], out["account_type"]) == (None, "expense")


def test_the_base_type_400_names_the_form_not_the_api(client, seed_accounts):
    folder = _new(client, "Deposits", "liability")
    child = _new(client, "Held", "liability", parent_id=folder["id"])
    r = client.put(f"/api/accounts/{child['id']}", json={"account_type": "asset"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "(Parent account: none, on its form)" in detail
    assert "parent_id" not in detail and "set its parent to none" not in detail


def test_a_pinned_parent_cannot_change_type_over_its_sub_accounts(
    client, seed_accounts
):
    ops = _new(client, "Spare bank", "asset", bank_kind="bank")
    _new(client, "Sweep", "asset", parent_id=ops["id"])
    r = client.put(
        f"/api/accounts/{ops['id']}",
        json={
            "account_type": "liability",
            "detail_type": "other_current_liability",
            "bank_kind": None,
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("its sub-account Sweep is Other Current Asset")
    assert "Move Sweep out from under Spare bank first" in detail


def test_a_parent_cannot_turn_pinned_over_sub_accounts_of_another_type(
    client, seed_accounts
):
    folder = _new(client, "Cash folder", "asset")
    _new(client, "Till", "asset", parent_id=folder["id"])
    r = client.put(
        f"/api/accounts/{folder['id']}",
        json={
            "account_type": "liability",
            "detail_type": "credit_card",
            "bank_kind": "credit_card",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith("its sub-account Till is an asset.")


def test_a_mixed_pair_saved_before_stays_editable(client, db_session, seed_accounts):
    """An imported expense under a bank account: the form sends the type on
    every save, which must not refuse a rename."""
    bank = seed_accounts["1000"]
    odd = Account(
        name="Old fees",
        account_type=AccountType.EXPENSE,
        parent_id=bank.id,
        is_system=False,
    )
    db_session.add(odd)
    db_session.commit()
    renamed = _ok(
        client.put(
            f"/api/accounts/{odd.id}",
            json={
                "name": "Bank fees",
                "account_type": "expense",
                "detail_type": "expense",
            },
        )
    )
    assert (renamed["name"], renamed["parent_id"]) == ("Bank fees", bank.id)


# --- the review's points (#416) ----------------------------------------------


def test_an_inactive_parent_is_refused_on_create_and_move(client, seed_accounts):
    """The form hides inactive accounts from the picker; the API refused one
    sent straight to it (#416). An account already under one still saves."""
    parent = _new(client, "Travel", account_number="8810")
    child = _new(client, "Hotel", parent_id=parent["id"])
    _ok(client.put(f"/api/accounts/{parent['id']}", json={"is_active": False}))

    r = client.post(
        "/api/accounts",
        json={"name": "Lodging", "account_type": "expense", "parent_id": parent["id"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "8810 Travel is inactive and cannot take sub-accounts. "
        "Reactivate it, or choose another parent."
    )

    other = _new(client, "Airfare")
    r = client.put(f"/api/accounts/{other['id']}", json={"parent_id": parent["id"]})
    assert r.status_code == 400
    assert "is inactive and cannot take sub-accounts" in r.json()["detail"]

    # the account already under the now-inactive parent: a full-object PUT
    # that resends that same parent is no move, so it still saves
    full = _ok(client.get(f"/api/accounts/{child['id']}"))
    body = {
        k: full[k]
        for k in ("name", "account_number", "account_type", "parent_id", "description")
    }
    body["name"] = "Hotel room"
    assert (
        _ok(client.put(f"/api/accounts/{child['id']}", json=body))["name"]
        == "Hotel room"
    )


def test_a_full_object_put_resending_the_parent_is_no_move(
    client, db_session, seed_accounts
):
    """A client that PUTs the whole account back, parent_id unchanged, on a
    pair saved before: not a move, so not refused."""
    bank = seed_accounts["1000"]
    odd = Account(
        name="Old fees",
        account_type=AccountType.EXPENSE,
        parent_id=bank.id,
        is_system=False,
    )
    db_session.add(odd)
    db_session.commit()
    full = _ok(client.get(f"/api/accounts/{odd.id}"))
    body = {
        k: full[k]
        for k in ("name", "account_number", "account_type", "parent_id", "description")
    }
    body["name"] = "Bank fees"
    assert _ok(client.put(f"/api/accounts/{odd.id}", json=body))["name"] == "Bank fees"


def test_a_type_change_resending_the_parent_says_how_to_get_out(client, seed_accounts):
    bank = seed_accounts["1000"]
    sweep = _new(client, "Sweep", "asset", parent_id=bank.id)
    r = client.put(
        f"/api/accounts/{sweep['id']}",
        json={
            "account_type": "expense",
            "detail_type": "expense",
            "parent_id": bank.id,
        },
    )
    assert r.status_code == 400
    assert "Move Sweep out from under" in r.json()["detail"]


def test_a_bank_parent_turning_plain_checks_its_sub_accounts(client, seed_accounts):
    """Nothing shares a Bank detail type, so the cascade skipped the
    sub-accounts; a Fixed Asset one under an Other Current Asset parent is a
    pair the API refuses everywhere else."""
    spare = _new(client, "Spare bank", "asset", bank_kind="bank")
    _new(client, "Safe", "asset", parent_id=spare["id"], detail_type="fixed_asset")
    r = client.put(
        f"/api/accounts/{spare['id']}",
        json={"detail_type": "other_current_asset", "bank_kind": None},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("its sub-account Safe is Fixed Asset, and under Other")
    assert "Move Safe out from under Spare bank first" in detail
    # one that shares the new type is no obstacle
    other = _new(client, "Spare bank 2", "asset", bank_kind="bank")
    _new(client, "Float", "asset", parent_id=other["id"])
    _ok(
        client.put(
            f"/api/accounts/{other['id']}",
            json={"detail_type": "other_current_asset", "bank_kind": None},
        )
    )


def test_a_control_account_takes_no_new_parent(client, db_session, seed_accounts):
    ar, bank = seed_accounts["1100"], seed_accounts["1000"]
    r = client.put(f"/api/accounts/{ar.id}", json={"parent_id": bank.id})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "1100 Accounts Receivable is found by its number when the books post, "
        "so it stays a top-level account."
    )
    # one it has already (saved before) can be left
    ar.parent_id = bank.id
    db_session.commit()
    _ok(client.put(f"/api/accounts/{ar.id}", json={"name": "Receivables"}))
    assert (
        _ok(client.put(f"/api/accounts/{ar.id}", json={"parent_id": None}))["parent_id"]
        is None
    )


def _chart(client, text, dry_run=0):
    import io

    r = client.post(
        f"/api/csv/import/accounts?dry_run={dry_run}",
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


CHART = (
    "Number,Name,Type,Parent\n"
    "8810,Bank fees,Expense,1000\n"
    "8830,Travel,Expense,4000\n"
    "8840,Petty cash,Bank,8830\n"
    "8850,Tolls,Expense,8830\n"
    "1100,Accounts Receivable,Asset,1000\n"
)


def test_the_chart_import_leaves_off_a_parent_the_api_refuses(
    client, db_session, seed_accounts
):
    for dry_run in (1, 0):
        out = _chart(client, CHART, dry_run)
        notes = {r["name"]: r.get("note", "") for r in out["rows"]}
        assert "must be an asset too" in notes["Bank fees"]
        assert "must be an income account too" in notes["Travel"]
        assert "must be an expense too" in notes["Petty cash"]
        assert "takes no parent" in notes["Accounts Receivable"]
        assert "left off" not in notes["Tolls"]
    db_session.expire_all()
    by = {a.account_number: a for a in db_session.query(Account).all()}
    assert by["8810"].parent_id is None
    assert by["8830"].parent_id is None
    assert by["8840"].parent_id is None
    assert by["8850"].parent_id == by["8830"].id
    assert by["1100"].parent_id is None


def test_a_bank_row_under_a_plain_folder_is_left_off(client, db_session, seed_accounts):
    out = _chart(
        client,
        "Number,Name,Type,Parent\n1700,Cash folder,Other Current Asset,\n"
        "1710,Sweep,Bank,1700\n1720,Float,Other Current Asset,1700\n",
        1,
    )
    notes = {r["name"]: r.get("note", "") for r in out["rows"]}
    assert "cannot be one" in notes["Sweep"] and "left off" in notes["Sweep"]
    assert "left off" not in notes["Float"]


def test_a_migration_skips_a_parent_link_of_another_type(db_session, seed_accounts):
    from datetime import date
    from decimal import Decimal

    from app.services.migration_common import run_import_bundle

    chart = [
        {
            "name": "Travel",
            "code": "8830",
            "type": AccountType.EXPENSE,
            "description": None,
        },
        {
            "name": "Hotel",
            "code": "8831",
            "type": AccountType.EXPENSE,
            "description": None,
            "parent": "Travel",
        },
        {
            "name": "Petty cash",
            "code": "1050",
            "type": AccountType.ASSET,
            "description": None,
            "parent": "Travel",
            "bank_kind": "bank",
        },
    ]
    gl = [
        [
            {
                "date": date(2026, 1, 5),
                "account": "Hotel",
                "debit": Decimal("10"),
                "credit": Decimal("0"),
                "journal": "J1",
            },
            {
                "date": date(2026, 1, 5),
                "account": "Petty cash",
                "debit": Decimal("0"),
                "credit": Decimal("10"),
                "journal": "J1",
            },
        ]
    ]
    parsers = {"coa": lambda _t: (chart, []), "gl": lambda _t: (gl, [])}
    out = run_import_bundle(
        db_session, {"coa": "x", "gl": "x"}, parsers, "test_import", "Test"
    )
    assert any(
        "'Petty cash' was not made a sub-account of 'Travel'" in w
        for w in out["warnings"]
    ), out
    by = {a.name: a for a in db_session.query(Account).all()}
    assert by["Hotel"].parent_id == by["Travel"].id
    assert by["Petty cash"].parent_id is None
