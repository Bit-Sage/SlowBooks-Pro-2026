"""A new account's default detail type follows 2.27.0's number and name
rule (2.28.0, the owner's call).

2.27.0's cash flow placed balance-sheet accounts by number and name; 2.28.0
places them by detail type, so a new "Truck Loan" made with the default
type counted as current. Now an account CREATED with no detail type gets
the one the old rule implies: an asset it calls non-current is a Fixed
Asset, a liability named like long-term debt a Long Term Liability. A
detail type given always wins; a sub-account of an ordinary parent takes
its parent's (#428 / #430); existing accounts are not retyped.
"""

import io

from app.models.accounts import Account, AccountType, default_detail_type
from app.services import cash_flow


def _post(client, **body):
    r = client.post("/api/accounts", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _account(db, account_id):
    db.expire_all()
    return db.get(Account, account_id)


def test_a_new_truck_loan_is_long_term_and_financing(client, db_session, seed_accounts):
    made = _post(client, name="Truck Loan", account_type="liability")
    assert made["detail_type"] == "long_term_liability"
    acct = _account(db_session, made["id"])
    assert cash_flow._place(acct, set(), set()) == "financing"


def test_a_delivery_van_numbered_1650_is_a_fixed_asset(
    client, db_session, seed_accounts
):
    # "van" is not in the equipment words: the 1500-1999 number decides
    made = _post(
        client, name="Delivery Van", account_type="asset", account_number="1650"
    )
    assert made["detail_type"] == "fixed_asset"
    acct = _account(db_session, made["id"])
    assert cash_flow._place(acct, set(), set()) == "investing"


def test_prepaid_insurance_numbered_1250_stays_current(client, seed_accounts):
    made = _post(
        client, name="Prepaid insurance", account_type="asset", account_number="1250"
    )
    assert made["detail_type"] == "other_current_asset"
    # a 1000-1499 number is current whatever the name says
    made = _post(
        client, name="Shop equipment", account_type="asset", account_number="1260"
    )
    assert made["detail_type"] == "other_current_asset"


def test_an_unnumbered_asset_named_like_equipment_and_accumulated_depreciation(
    client, seed_accounts
):
    assert (
        _post(client, name="Office Furniture", account_type="asset")["detail_type"]
        == "fixed_asset"
    )
    assert (
        _post(client, name="Accum. Depr. - Vans", account_type="asset")["detail_type"]
        == "fixed_asset"
    )
    assert (
        _post(client, name="Employee advances", account_type="asset")["detail_type"]
        == "other_current_asset"
    )


def test_an_explicit_detail_type_wins(client, seed_accounts):
    made = _post(
        client,
        name="Truck Loan (12 months)",
        account_type="liability",
        detail_type="other_current_liability",
    )
    assert made["detail_type"] == "other_current_liability"
    made = _post(
        client,
        name="Delivery Van",
        account_type="asset",
        account_number="1660",
        detail_type="other_asset",
    )
    assert made["detail_type"] == "other_asset"


def test_a_credit_card_named_like_a_loan_stays_a_card(client, seed_accounts):
    made = _post(
        client,
        name="Loan Card",
        account_type="liability",
        bank_kind="credit_card",
    )
    assert made["detail_type"] == "credit_card"


def test_a_sub_account_takes_its_parents_not_the_name_rule(
    client, db_session, seed_accounts
):
    """Inheritance first for a sub-account (#428): a "Truck Loan" under 2500
    Other Current Liabilities shares its parent's, so the family prints
    together; under a pinned parent (a card) it stands alone and the rule
    picks."""
    parent = seed_accounts["2500"]
    made = _post(
        client, name="Truck Loan", account_type="liability", parent_id=parent.id
    )
    assert made["detail_type"] == "other_current_liability"
    card = _post(
        client, name="Fleet card", account_type="liability", bank_kind="credit_card"
    )
    made = _post(
        client, name="Equipment loan", account_type="liability", parent_id=card["id"]
    )
    assert made["detail_type"] == "long_term_liability"


def test_the_model_hook_gives_a_direct_account_the_same_default(
    db_session, seed_accounts
):
    loan = Account(name="Mortgage", account_type=AccountType.LIABILITY)
    van = Account(name="Van", account_type=AccountType.ASSET, account_number="1655")
    told = Account(
        name="Bank loan",
        account_type=AccountType.LIABILITY,
        detail_type="other_current_liability",
    )
    db_session.add_all([loan, van, told])
    db_session.flush()
    assert loan.detail_type == "long_term_liability"
    assert van.detail_type == "fixed_asset"
    assert told.detail_type == "other_current_liability"
    # a direct sub-account takes its ordinary parent's, as the API does
    child = Account(
        name="Truck Loan",
        account_type=AccountType.LIABILITY,
        parent_id=seed_accounts["2500"].id,
    )
    db_session.add(child)
    db_session.flush()
    assert child.detail_type == "other_current_liability"


def test_an_existing_account_is_not_retyped(client, db_session, seed_accounts):
    """Editing an account (rename it like a loan) never runs the rule: the
    rule is for accounts being created; the type stays editable."""
    made = _post(client, name="Customer deposits", account_type="liability")
    assert made["detail_type"] == "other_current_liability"
    r = client.put(f"/api/accounts/{made['id']}", json={"name": "Customer loan"})
    assert r.status_code == 200, r.text
    assert r.json()["detail_type"] == "other_current_liability"
    r = client.put(
        f"/api/accounts/{made['id']}", json={"detail_type": "long_term_liability"}
    )
    assert r.status_code == 200 and r.json()["detail_type"] == "long_term_liability"


def test_a_control_account_keeps_its_control_default():
    # 2200 Sales Tax Payable named like a loan is still the control default
    assert (
        default_detail_type("liability", None, "2200", name="Sales tax loan").value
        == "other_current_liability"
    )
    assert default_detail_type("liability", None, None, name="Truck loan").value == (
        "long_term_liability"
    )
    # no name: the type's plain default (a retype, not a create)
    assert default_detail_type("liability", None, None).value == (
        "other_current_liability"
    )


def test_a_chart_csv_row_gets_the_same_defaults(client, db_session, seed_accounts):
    text = (
        "Number,Name,Type\n"
        "2450,Truck Loan,liability\n"
        "1650,Delivery Van,asset\n"
        "1250,Prepaid insurance,asset\n"
    )
    plan = client.post(
        "/api/csv/import/accounts",
        files={"file": ("chart.csv", io.BytesIO(text.encode()), "text/csv")},
    ).json()
    planned = {r["number"]: r["detail_type"] for r in plan["rows"]}
    want = {
        "2450": "long_term_liability",
        "1650": "fixed_asset",
        "1250": "other_current_asset",
    }
    assert planned == want  # the dry run says what apply writes
    out = client.post(
        "/api/csv/import/accounts?dry_run=0",
        files={"file": ("chart.csv", io.BytesIO(text.encode()), "text/csv")},
    ).json()
    assert out["errors"] == [] and out["created"] == 3
    db_session.expire_all()
    got = {
        a.account_number: a.detail_type
        for a in db_session.query(Account).filter(
            Account.account_number.in_(want.keys())
        )
    }
    assert got == want
    # an explicit Detail type column wins
    text = "Number,Name,Type,Detail type\n2460,Van Loan,liability,Other Current Liability\n"
    out = client.post(
        "/api/csv/import/accounts?dry_run=0",
        files={"file": ("chart.csv", io.BytesIO(text.encode()), "text/csv")},
    ).json()
    assert out["errors"] == []
    db_session.expire_all()
    van = db_session.query(Account).filter(Account.account_number == "2460").one()
    assert van.detail_type == "other_current_liability"


def test_an_iif_import_keeps_the_files_type_and_a_dialect_import_gets_the_default(
    db_session, seed_accounts
):
    """The IIF lists name QuickBooks' type on every account, so the file's
    wins (an OCLIAB "Truck Loan" is current, an LTLIAB one long-term); the
    shared importer engine, for a file with no finer type, gets the rule."""
    from app.services.iif_import import import_all

    iif = (
        "!ACCNT\tNAME\tACCNTTYPE\tACCNUM\r\n"
        "ACCNT\tTruck Loan\tLTLIAB\t2470\r\n"
        "ACCNT\tShort Truck Loan\tOCLIAB\t2480\r\n"
        "ACCNT\tDelivery Van\tFIXASSET\t1670\r\n"
    )
    result = import_all(db_session, iif)
    db_session.commit()
    assert not result["errors"], result["errors"]
    got = {
        a.name: a.detail_type
        for a in db_session.query(Account).filter(
            Account.account_number.in_(("2470", "2480", "1670"))
        )
    }
    assert got == {
        "Truck Loan": "long_term_liability",
        "Short Truck Loan": "other_current_liability",
        "Delivery Van": "fixed_asset",
    }

    from app.services.migration_common import _effective_detail

    for spec, number, want in (
        ({"name": "Truck Loan", "type": "liability"}, None, "long_term_liability"),
        ({"name": "Delivery Van", "type": "asset"}, "1650", "fixed_asset"),
        ({"name": "Prepaid insurance", "type": "asset"}, "1250", "other_current_asset"),
        (
            {
                "name": "Truck Loan",
                "type": "liability",
                "detail_type": "other_current_liability",
            },
            None,
            "other_current_liability",
        ),
    ):
        assert _effective_detail(spec, number, None, False, None) == want, spec
