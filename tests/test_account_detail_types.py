"""Account detail types (#390): QuickBooks' fifteen types on the chart.

The detail type refines account_type and may never contradict it; a bank or
card account is Bank / Credit Card and nothing else is; Accounts Receivable
and Payable are 1100 and 2000 alone; the fifteen control accounts keep
theirs; a sub-account shares its parent's. Every path that builds an
Account directly gets one from the model.
"""

import pytest

from app.models.accounts import Account, AccountType


def _create(client, **body):
    return client.post("/api/accounts", json=body)


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


# --- the list -----------------------------------------------------------------


def test_the_fifteen_detail_types_in_picker_order(client):
    rows = _ok(client.get("/api/accounts/detail-types"))
    assert [r["value"] for r in rows] == [
        "bank",
        "accounts_receivable",
        "other_current_asset",
        "fixed_asset",
        "other_asset",
        "accounts_payable",
        "credit_card",
        "other_current_liability",
        "long_term_liability",
        "equity",
        "income",
        "other_income",
        "cost_of_goods_sold",
        "expense",
        "other_expense",
    ]
    by = {r["value"]: r for r in rows}
    assert by["long_term_liability"] == {
        "value": "long_term_liability",
        "label": "Long Term Liability",
        "account_type": "liability",
        "control_only": False,
    }
    assert {v for v, r in by.items() if r["control_only"]} == {
        "accounts_receivable",
        "accounts_payable",
    }


# --- create -------------------------------------------------------------------


@pytest.mark.parametrize(
    "account_type,bank_kind,expected",
    [
        ("asset", None, "other_current_asset"),
        ("asset", "bank", "bank"),
        ("liability", None, "other_current_liability"),
        ("liability", "credit_card", "credit_card"),
        ("equity", None, "equity"),
        ("income", None, "income"),
        ("cogs", None, "cost_of_goods_sold"),
        ("expense", None, "expense"),
    ],
)
def test_an_account_sent_without_a_detail_type_gets_the_default(
    client, account_type, bank_kind, expected
):
    body = {"name": f"Plain {account_type}", "account_type": account_type}
    if bank_kind:
        body["bank_kind"] = bank_kind
    a = _ok(_create(client, **body), 201)
    assert a["detail_type"] == expected
    assert a["detail_type_label"]


def test_a_detail_type_is_checked_against_the_type(client):
    r = _create(
        client,
        name="Truck",
        account_type="asset",
        detail_type="long_term_liability",
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "detail_type 'long_term_liability' is a liability detail type; the "
        "account's type is 'asset'"
    )
    a = _ok(
        _create(client, name="Truck", account_type="asset", detail_type="fixed_asset"),
        201,
    )
    assert (a["detail_type"], a["detail_type_label"]) == ("fixed_asset", "Fixed Asset")


def test_bank_and_card_go_with_bank_kind_both_ways(client):
    r = _create(client, name="Till", account_type="asset", detail_type="bank")
    assert r.status_code == 400 and "bank_kind 'bank'" in r.json()["detail"]
    r = _create(
        client,
        name="Till",
        account_type="asset",
        bank_kind="bank",
        detail_type="other_current_asset",
    )
    assert r.status_code == 400 and "is a Bank" in r.json()["detail"]
    r = _create(
        client, name="Visa", account_type="liability", detail_type="credit_card"
    )
    assert r.status_code == 400
    a = _ok(
        _create(
            client,
            name="Visa",
            account_type="liability",
            bank_kind="credit_card",
            detail_type="credit_card",
        ),
        201,
    )
    assert a["detail_type"] == "credit_card"


def test_receivable_and_payable_are_kept_for_1100_and_2000(client):
    r = _create(
        client,
        name="Second AR",
        account_number="1150",
        account_type="asset",
        detail_type="accounts_receivable",
    )
    assert r.status_code == 400
    assert "kept for 1100" in r.json()["detail"]
    r = _create(
        client,
        name="Second AP",
        account_type="liability",
        detail_type="accounts_payable",
    )
    assert r.status_code == 400 and "kept for 2000" in r.json()["detail"]
    # restoring 1100 itself (MissingControlAccount says to) gives it AR
    a = _ok(
        _create(
            client,
            name="Accounts Receivable",
            account_number="1100",
            account_type="asset",
        ),
        201,
    )
    assert a["detail_type"] == "accounts_receivable"


def test_a_control_number_is_created_with_its_own_detail_type(client):
    r = _create(
        client,
        name="Late Fee Income",
        account_number="4800",
        account_type="income",
        detail_type="other_income",
    )
    assert r.status_code == 400
    assert "4800 Late Fee Income is a control account" in r.json()["detail"]


# --- seeded and built directly ------------------------------------------------


def test_the_seeded_chart_carries_its_detail_types(client, seed_accounts):
    got = {
        a["account_number"]: a["detail_type"] for a in _ok(client.get("/api/accounts"))
    }
    assert got["1000"] == "bank" and got["1010"] == "bank"
    assert got["1100"] == "accounts_receivable"
    assert got["1200"] == "other_current_asset"
    assert got["1500"] == got["1510"] == got["1600"] == "fixed_asset"
    assert got["1700"] == "other_asset"
    assert got["2000"] == "accounts_payable"
    assert got["2100"] == "credit_card"
    assert got["2400"] == "long_term_liability"
    assert got["2500"] == "other_current_liability"
    assert got["4900"] == "other_income"
    assert got["5000"] == "cost_of_goods_sold"
    assert got["6000"] == "expense"


def test_an_account_built_directly_gets_a_detail_type(db_session):
    """About ten paths build Account(...) themselves; none may hit NOT NULL."""
    a = Account(name="Built here", account_type=AccountType.LIABILITY)
    card = Account(
        name="Built card", account_type=AccountType.LIABILITY, bank_kind="credit_card"
    )
    ar = Account(name="AR again", account_number="1100", account_type=AccountType.ASSET)
    db_session.add_all([a, card, ar])
    db_session.flush()
    assert a.detail_type == "other_current_liability"
    assert card.detail_type == "credit_card"
    assert ar.detail_type == "accounts_receivable"
    # one that does not fit falls back to the default rather than fail
    odd = Account(
        name="Odd", account_type=AccountType.EXPENSE, detail_type="fixed_asset"
    )
    db_session.add(odd)
    db_session.flush()
    assert odd.detail_type == "expense"


def test_the_fx_and_disposal_accounts_are_other_expense(db_session, seed_accounts):
    from app.services.currency import fx_gain_loss_account_id
    from app.services.fixed_assets import _disposal_account_id

    fx = db_session.get(Account, fx_gain_loss_account_id(db_session))
    disposal = db_session.get(Account, _disposal_account_id(db_session))
    assert (fx.account_number, fx.detail_type) == ("6999", "other_expense")
    assert (disposal.account_number, disposal.detail_type) == (
        "7999",
        "other_expense",
    )


def test_the_on_demand_accounts_take_their_defaults(db_session, seed_accounts):
    from app.services.accounting import ensure_account
    from app.services.benefits_engine import ensure_accounts

    obe = ensure_account(
        db_session, "3900", "Opening Balance Equity", AccountType.EQUITY
    )
    assert obe.detail_type == "equity"
    ensure_accounts(db_session)
    pto = db_session.query(Account).filter_by(account_number="2390").one()
    assert pto.detail_type == "other_current_liability"


def test_a_sub_account_built_directly_takes_its_parents(db_session, seed_accounts):
    parent = seed_accounts["1500"]
    child = Account(name="Trailer", account_type=AccountType.ASSET, parent=parent)
    db_session.add(child)
    db_session.flush()
    assert child.detail_type == "fixed_asset"
    # a control account keeps its own under any folder
    folder = Account(name="Current Assets", account_type=AccountType.ASSET)
    db_session.add(folder)
    db_session.flush()
    ar = seed_accounts["1100"]
    ar.parent_id = folder.id
    db_session.flush()
    assert ar.detail_type == "accounts_receivable"


# --- update -------------------------------------------------------------------


def test_the_detail_type_changes_and_is_checked_on_update(client):
    a = _ok(_create(client, name="Shop truck", account_type="asset"), 201)
    assert a["detail_type"] == "other_current_asset"
    a = _ok(client.put(f"/api/accounts/{a['id']}", json={"detail_type": "fixed_asset"}))
    assert a["detail_type"] == "fixed_asset"
    r = client.put(f"/api/accounts/{a['id']}", json={"detail_type": "expense"})
    assert r.status_code == 400
    assert "is an expense detail type" in r.json()["detail"]
    r = client.put(f"/api/accounts/{a['id']}", json={"detail_type": None})
    assert r.status_code == 422


def test_a_type_change_resets_the_detail_type(client):
    a = _ok(
        _create(
            client, name="Truck note", account_type="asset", detail_type="fixed_asset"
        ),
        201,
    )
    a = _ok(client.put(f"/api/accounts/{a['id']}", json={"account_type": "liability"}))
    assert (a["account_type"], a["detail_type"]) == (
        "liability",
        "other_current_liability",
    )
    a = _ok(
        client.put(
            f"/api/accounts/{a['id']}",
            json={"account_type": "asset", "detail_type": "other_asset"},
        )
    )
    assert a["detail_type"] == "other_asset"
    r = client.put(
        f"/api/accounts/{a['id']}",
        json={"account_type": "income", "detail_type": "other_asset"},
    )
    assert r.status_code == 400


def test_a_bank_kind_change_carries_the_detail_type(client):
    a = _ok(_create(client, name="Petty cash", account_type="asset"), 201)
    a = _ok(client.put(f"/api/accounts/{a['id']}", json={"bank_kind": "bank"}))
    assert a["detail_type"] == "bank"
    a = _ok(client.put(f"/api/accounts/{a['id']}", json={"bank_kind": None}))
    assert a["detail_type"] == "other_current_asset"
    r = client.put(f"/api/accounts/{a['id']}", json={"detail_type": "bank"})
    assert r.status_code == 400 and "bank_kind 'bank'" in r.json()["detail"]
    a = _ok(
        client.put(
            f"/api/accounts/{a['id']}",
            json={"detail_type": "bank", "bank_kind": "bank"},
        )
    )
    assert (a["bank_kind"], a["detail_type"]) == ("bank", "bank")


@pytest.mark.parametrize(
    "number,other",
    [
        ("1000", "other_current_asset"),
        ("1100", "other_current_asset"),
        ("1200", "fixed_asset"),
        ("1300", "other_asset"),
        ("2000", "other_current_liability"),
        ("2100", "other_current_liability"),
        ("2200", "long_term_liability"),
        ("3200", "equity"),
        ("4000", "other_income"),
        ("4800", "other_income"),
        ("5000", "cost_of_goods_sold"),
        ("5900", "other_expense"),
        ("6000", "other_expense"),
        ("6120", "other_expense"),
        ("6150", "other_expense"),
    ],
)
def test_every_control_accounts_detail_type_is_locked(
    client, db_session, seed_accounts, number, other
):
    from app.services import control_accounts

    assert number in control_accounts.CONTROL_ACCOUNTS
    acct = seed_accounts.get(number)
    if acct is None:  # 4800 and 5900 are created on demand
        name, _ = control_accounts.describe(number)
        atype = AccountType.INCOME if number == "4800" else AccountType.EXPENSE
        acct = Account(name=name, account_number=number, account_type=atype)
        db_session.add(acct)
        db_session.commit()
    before = acct.detail_type
    if other == before:  # 3200 Retained Earnings: equity has one detail type
        other = "other_asset"
    r = client.put(f"/api/accounts/{acct.id}", json={"detail_type": other})
    assert r.status_code == 400, r.text
    assert "control account" in r.json()["detail"]
    # the same detail type, and a rename, still go through
    _ok(client.put(f"/api/accounts/{acct.id}", json={"detail_type": before}))
    _ok(client.put(f"/api/accounts/{acct.id}", json={"name": acct.name + " (ours)"}))
    db_session.refresh(acct)
    assert acct.detail_type == before


def test_a_control_account_with_a_stray_detail_type_can_still_be_renamed(
    client, db_session, seed_accounts
):
    """A file written before the lock could carry one that does not fit; a
    rename is not refused for it, and puts the expected one back."""
    from sqlalchemy import text

    income = seed_accounts["4000"]
    db_session.execute(
        text("UPDATE accounts SET detail_type = 'other_income' WHERE id = :i"),
        {"i": income.id},
    )
    db_session.commit()
    a = _ok(client.put(f"/api/accounts/{income.id}", json={"name": "Sign Sales"}))
    assert (a["name"], a["detail_type"]) == ("Sign Sales", "income")


def test_the_card_control_account_keeps_its_bank_kind(client, seed_accounts):
    card = seed_accounts["2100"]
    r = client.put(f"/api/accounts/{card.id}", json={"bank_kind": None})
    assert r.status_code == 400
    assert "Its detail type stays Credit Card" in r.json()["detail"]


# --- sub-accounts -------------------------------------------------------------


def test_a_sub_account_shares_its_parents_detail_type(client, seed_accounts):
    equipment = seed_accounts["1500"]
    child = _ok(
        _create(
            client, name="Skid steer", account_type="asset", parent_id=equipment.id
        ),
        201,
    )
    assert child["detail_type"] == "fixed_asset"
    r = _create(
        client,
        name="Spare parts",
        account_type="asset",
        parent_id=equipment.id,
        detail_type="other_current_asset",
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "a sub-account shares its parent's detail type, except under a bank, "
        "card, Accounts Receivable or Accounts Payable parent; a sub-account "
        "of 1500 Equipment (Fixed Asset) must be a Fixed Asset too"
    )
    r = _create(client, name="Fuel", account_type="expense", parent_id=equipment.id)
    assert r.status_code == 400
    assert r.json()["detail"].startswith(
        "a sub-account shares its parent's detail type, except under a bank, "
        "card, Accounts Receivable or Accounts Payable parent; a sub-account "
        "of 1500 Equipment (Fixed Asset) must be a Fixed Asset too: "
    )
    # changing the sub-account's own detail type away from its parent's
    r = client.put(
        f"/api/accounts/{child['id']}", json={"detail_type": "other_current_asset"}
    )
    assert r.status_code == 400 and "sub-account of 1500" in r.json()["detail"]


def test_a_new_parent_hands_its_detail_type_down(client, seed_accounts):
    loose = _ok(_create(client, name="Generator", account_type="asset"), 201)
    assert loose["detail_type"] == "other_current_asset"
    moved = _ok(
        client.put(
            f"/api/accounts/{loose['id']}",
            json={"parent_id": seed_accounts["1600"].id},
        )
    )
    assert moved["detail_type"] == "fixed_asset"
    r = client.put(
        f"/api/accounts/{loose['id']}",
        json={"parent_id": seed_accounts["1200"].id, "detail_type": "fixed_asset"},
    )
    assert r.status_code == 400
    r = client.put(
        f"/api/accounts/{loose['id']}", json={"parent_id": seed_accounts["6000"].id}
    )
    assert r.status_code == 400 and "Advertising" in r.json()["detail"]


def test_a_control_account_cannot_go_under_a_parent_of_another_detail_type(
    client, seed_accounts
):
    folder = _ok(_create(client, name="Current Assets", account_type="asset"), 201)
    r = client.put(
        f"/api/accounts/{seed_accounts['1100'].id}", json={"parent_id": folder["id"]}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "a sub-account shares its parent's detail type, except under a bank, "
        "card, Accounts Receivable or Accounts Payable parent; a sub-account "
        "of Current Assets (Other Current Asset) must be an Other "
        "Current Asset too: 1100 Accounts Receivable is a control account; its "
        "detail type is Accounts Receivable"
    )


def test_a_parents_new_detail_type_goes_to_its_sub_accounts(client, seed_accounts):
    loan = seed_accounts["2400"]
    child = _ok(
        _create(client, name="Truck loan", account_type="liability", parent_id=loan.id),
        201,
    )
    assert child["detail_type"] == "long_term_liability"
    _ok(
        client.put(
            f"/api/accounts/{loan.id}", json={"detail_type": "other_current_liability"}
        )
    )
    assert (
        _ok(client.get(f"/api/accounts/{child['id']}"))["detail_type"]
        == "other_current_liability"
    )


def test_a_parent_whose_sub_account_cannot_follow_is_refused(
    client, db_session, seed_accounts
):
    folder = _ok(_create(client, name="Cards", account_type="liability"), 201)
    # a card under a plain folder: possible before #390, and kept
    card = Account(
        name="Amex",
        account_type=AccountType.LIABILITY,
        bank_kind="credit_card",
        parent_id=folder["id"],
    )
    db_session.add(card)
    db_session.commit()
    assert card.detail_type == "credit_card"
    # a sibling that shares the folder's would have to follow, and can
    plain = _ok(
        _create(
            client, name="Accrued", account_type="liability", parent_id=folder["id"]
        ),
        201,
    )
    assert plain["detail_type"] == "other_current_liability"
    _ok(
        client.put(
            f"/api/accounts/{folder['id']}", json={"detail_type": "long_term_liability"}
        )
    )
    assert (
        _ok(client.get(f"/api/accounts/{plain['id']}"))["detail_type"]
        == "long_term_liability"
    )
    db_session.refresh(card)
    assert card.detail_type == "credit_card"  # it never shared the folder's
    # a type change the sub-account cannot follow is refused, naming it
    r = client.put(f"/api/accounts/{folder['id']}", json={"account_type": "asset"})
    assert r.status_code == 400
    assert "its sub-account Accrued" in r.json()["detail"]


# --- list and export ----------------------------------------------------------


def test_the_list_filters_on_detail_type(client, seed_accounts):
    rows = _ok(client.get("/api/accounts?detail_type=fixed_asset"))
    assert sorted(r["account_number"] for r in rows) == ["1500", "1510", "1600"]


def test_the_chart_csv_export_has_a_detail_type_column(client, seed_accounts):
    text = client.get("/api/csv/export/accounts").text.lstrip("\ufeff")
    header = text.splitlines()[0].split(",")
    assert header[:4] == ["Number", "Name", "Type", "Detail type"]
    line = next(ln for ln in text.splitlines() if ln.startswith("2400,"))
    assert line.split(",")[2:4] == ["liability", "Long Term Liability"]
