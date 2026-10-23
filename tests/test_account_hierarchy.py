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
    assert r.json()["detail"].startswith("a sub-account of Sales (Income) must be")


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
    assert r.json()["detail"].startswith("its sub-account Sweep is an asset.")


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
