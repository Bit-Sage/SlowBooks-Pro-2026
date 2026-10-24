"""A default-typed sub-account prints in its parent's Balance Sheet group
(2.28.0, the owner's call).

The upgrade kept a default-typed sub-account where 2.27.0's cash flow had
it (#390), so "1520 Trucks" under "1500 Equipment" printed under Other
Assets, and "Accrued Interest" under "2400 Loan Payable" under Current
Liabilities, away from their parents. On the Balance Sheet and the
Statement of Financial Position (screen, PDF, CSV) such a sub-account now
prints in its parent's group, inside its family. Display only: the stored
detail type, the cash flow and every figure stay; a group's total moves by
that line's amount and the statement still balances. A sub-account typed
to something else keeps its own group.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.services import cash_flow
from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of

AS_OF = "2026-12-31"


def _je(db, day, lines):
    create_journal_entry(
        db,
        day,
        "je",
        [
            {"account_id": a.id, "debit": Decimal(str(dr)), "credit": Decimal(str(cr))}
            for a, dr, cr in lines
        ],
    )


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)

    def add(key, number, name, atype, detail, parent):
        acct = Account(
            account_number=number,
            name=name,
            account_type=atype,
            detail_type=detail,
            parent_id=a[parent].id,
        )
        db_session.add(acct)
        db_session.flush()
        a[key] = acct

    # as the upgrade left them: the backfill's Other Asset for a 1500-1999
    # number not named like equipment, and the type's default
    add("trucks", "1520", "Trucks", AccountType.ASSET, "other_asset", "1500")
    add(
        "shelving",
        None,
        "Shop Shelving",
        AccountType.ASSET,
        "other_current_asset",
        "1500",
    )
    add(
        "accrued",
        None,
        "Accrued Interest",
        AccountType.LIABILITY,
        "other_current_liability",
        "2400",
    )
    # typed on purpose to something its rules would not give it
    add("escrow", None, "Equipment escrow", AccountType.ASSET, "other_asset", "1500")
    cash = a["1000"]
    d = date(2026, 3, 1)
    _je(db_session, d, [(cash, 50000, 0), (a["2400"], 0, 50000)])
    _je(db_session, d, [(a["1500"], 4000, 0), (cash, 0, 4000)])
    _je(db_session, d, [(a["trucks"], 30000, 0), (cash, 0, 30000)])
    _je(db_session, d, [(a["shelving"], 1200, 0), (cash, 0, 1200)])
    _je(db_session, d, [(a["escrow"], 700, 0), (cash, 0, 700)])
    _je(db_session, d, [(a["6000"], 300, 0), (a["accrued"], 0, 300)])
    _je(db_session, d, [(a["1400"], 250, 0), (cash, 0, 250)])
    _je(db_session, date(2026, 5, 1), [(cash, 9000, 0), (a["4000"], 0, 9000)])
    db_session.commit()
    return a


def _bs(client, qs=""):
    r = client.get(f"/api/reports/balance-sheet?as_of_date={AS_OF}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _shape(groups):
    return {
        g["heading"]: [i["account_number"] or i["account_name"] for i in g["lines"]]
        for g in groups
    }


def test_the_trucks_print_under_fixed_assets_with_their_parent(client, books):
    d = _bs(client)
    assets = _shape(d["groups"]["assets"])
    # inside the parent's family: right after 1500 Equipment
    assert assets["Fixed Assets"][:3] == ["1500", "1520", "Shop Shelving"]
    assert "1520" not in assets.get("Other Assets", [])
    assert "Shop Shelving" not in assets["Current Assets"]
    # the explicitly typed sub-account keeps its own group
    assert assets["Other Assets"] == ["Equipment escrow"]
    trucks = next(i for i in d["assets"] if i["account_number"] == "1520")
    # the account's own detail type is reported as it is stored
    assert trucks["detail_type"] == "other_asset"
    assert trucks["group_detail_type"] == "fixed_asset"


def test_a_default_child_of_a_long_term_liability_prints_with_it(client, books):
    d = _bs(client)
    liabilities = _shape(d["groups"]["liabilities"])
    assert liabilities["Long Term Liabilities"] == ["2400", "Accrued Interest"]
    assert "Accrued Interest" not in liabilities.get("Current Liabilities", [])
    assert d["total_long_term_liabilities"] == 50300
    assert d["total_current_liabilities"] == 0


def test_the_totals_still_tie_and_the_statement_balances(client, books):
    d = _bs(client)
    # 1500 Equipment, 1520 Trucks and Shop Shelving (1510 / 1600 hold nothing)
    assert d["total_fixed_assets"] == 4000 + 30000 + 1200
    assert d["total_other_assets"] == 700
    assert d["total_current_assets"] == round(
        d["total_assets"] - d["total_fixed_assets"] - d["total_other_assets"], 2
    )
    for side, total in (
        ("assets", "total_assets"),
        ("liabilities", "total_liabilities"),
    ):
        assert round(sum(d[g["total_key"]] for g in d["groups"][side]), 2) == d[total]
        assert (
            round(sum(i["amount"] for g in d["groups"][side] for i in g["lines"]), 2)
            == d[total]
        )
    assert d["total_assets"] == round(d["total_liabilities"] + d["total_equity"], 2)


def test_nothing_is_retyped_and_the_cash_flow_does_not_move(client, db_session, books):
    _bs(client)
    db_session.expire_all()
    assert db_session.get(Account, books["trucks"].id).detail_type == "other_asset"
    accrued = db_session.get(Account, books["accrued"].id)
    shelving = db_session.get(Account, books["shelving"].id)
    assert accrued.detail_type == "other_current_liability"
    assert cash_flow._place(accrued, set(), set()) == "working_capital"
    assert cash_flow._place(shelving, set(), set()) == "working_capital"


def test_the_pdf_csv_and_prior_year_follow(client, books, monkeypatch):
    rows = rows_of(
        client.get(f"/api/reports/balance-sheet/csv?as_of_date={AS_OF}").text
    )
    trucks = next(r for r in rows if r[1] == "1520")
    assert trucks[0] == "Fixed Assets"
    accrued = next(r for r in rows if r[2].endswith("Accrued Interest"))
    assert accrued[0] == "Long Term Liabilities"

    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?as_of_date={AS_OF}")
    words = [r["cells"][0].strip() for r in captured["s"][0]["rows"]]
    fixed = words.index("Fixed Assets")
    assert any("Trucks" in w for w in words[fixed : words.index("Total Fixed Assets")])

    d = _bs(client, "&compare=prior_year")
    assets = _shape(d["groups"]["assets"])
    assert "1520" in assets["Fixed Assets"]
    assert d["prior"]["total_fixed_assets"] == 0


def test_the_statement_of_financial_position_follows_too(client, db_session, books):
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    d = client.get(
        f"/api/reports/statement-of-financial-position?as_of_date={AS_OF}"
    ).json()
    assets = _shape(d["groups"]["assets"])
    liabilities = _shape(d["groups"]["liabilities"])
    assert (
        "1520" in assets["Fixed Assets"] and "Shop Shelving" in assets["Fixed Assets"]
    )
    assert assets["Other Assets"] == ["Equipment escrow"]
    assert "Accrued Interest" in liabilities["Long Term Liabilities"]
    assert d["total_fixed_assets"] == 35200
    assert round(sum(d[g["total_key"]] for g in d["groups"]["assets"]), 2) == (
        d["total_assets"]
    )


def test_a_sub_account_under_a_bank_keeps_its_own_group(
    client, db_session, seed_accounts
):
    """Under a bank, card, 1100 or 2000 a sub-account is set on its own
    (QuickBooks' rule): a default-typed one stays where its type puts it."""
    from app.services.statement_layout import bs_print_details

    cash = seed_accounts["1000"]
    sweep = Account(
        name="Sweep holding",
        account_type=AccountType.ASSET,
        detail_type="other_current_asset",
        parent_id=cash.id,
    )
    db_session.add(sweep)
    db_session.commit()
    assert sweep.id not in bs_print_details(db_session)
