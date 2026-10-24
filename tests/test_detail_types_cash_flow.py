"""The cash flow statement reads the account detail type (#390, PR 2).

`cash_flow._place` guessed current from non-current by the number range
and the name; it reads the detail type now. The reviewer's warning on
PR #403: the old rules also used the fixed-asset register and the
equipment name rule, so the detail types the upgrade gave must put every
account where 2.26.0 did. Proven here on a whole statement: the same books
with the backfilled detail types give the same cash flow figure for figure
under the 2.26.0 placement and the new one.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.fixed_assets import FixedAssetType
from app.services import cash_flow
from app.services.accounting import create_journal_entry
from tests.cash_flow_2_26 import migration, place_2_26

START, END = date(2026, 1, 1), date(2026, 12, 31)


def _je(db, day, lines, what="je"):
    create_journal_entry(
        db,
        day,
        what,
        [
            {"account_id": a.id, "debit": Decimal(str(dr)), "credit": Decimal(str(cr))}
            for a, dr, cr in lines
        ],
    )


def _upgraded(db, accounts):
    """Give every account the detail type the upgrade's backfill gives it,
    as a 2.26 company file has after migrating."""
    fixed, accumulated = set(), set()
    for t in db.query(FixedAssetType).all():
        fixed.add(t.asset_account_id)
        accumulated.add(t.accumulated_depreciation_account_id)
    rows = [
        {
            "id": a.id,
            "name": a.name,
            "account_number": a.account_number,
            "account_type": a.account_type.name,
            "bank_kind": a.bank_kind,
            "parent_id": a.parent_id,
        }
        for a in db.query(Account).all()
    ]
    result, _ = migration().backfill(rows, fixed, accumulated)
    for a in db.query(Account).all():
        a.detail_type = result[a.id]
    db.commit()
    return result


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)
    db = db_session

    def add(key, number, name, atype, parent=None):
        acct = Account(
            account_number=number,
            name=name,
            account_type=atype,
            parent_id=parent.id if parent else None,
        )
        db.add(acct)
        db.flush()
        a[key] = acct
        return acct

    # register-linked, numbered and named like nothing in particular
    add("register", "1450.1", "Shop Truck 2024", AccountType.ASSET)
    # imported, unnumbered, named like equipment
    add("imported_eq", None, "Heavy Equipment", AccountType.ASSET)
    # the seeded 1500 range renamed: investing by its number
    add("tools", "1550", "Tools", AccountType.ASSET)
    # a numbered current asset named like equipment: current, as it was
    add("deposits", "1450", "Equipment Deposits", AccountType.ASSET)
    # imported, unnumbered
    add("mortgage", None, "Mortgage on Shop", AccountType.LIABILITY)
    add("accrued", None, "Accrued Wages", AccountType.LIABILITY)
    # default-named sub-accounts of non-current parents: the backfill's
    # parent inheritance must not move them out of working capital
    add("shelving", None, "Shop Shelving", AccountType.ASSET, a["1500"])
    add("interest", None, "Accrued Interest", AccountType.LIABILITY, a["2400"])
    db.add(
        FixedAssetType(
            name="Trucks",
            asset_account_id=a["register"].id,
            accumulated_depreciation_account_id=a["1510"].id,
            depreciation_expense_account_id=a["6000"].id,
        )
    )
    db.commit()

    cash = a["1000"]
    d = date(2026, 3, 1)
    _je(db, d, [(cash, 50000, 0), (a["2400"], 0, 50000)], "2400 loan")
    _je(db, d, [(cash, 80000, 0), (a["mortgage"], 0, 80000)], "mortgage")
    _je(db, d, [(a["register"], 30000, 0), (cash, 0, 30000)], "truck")
    _je(db, d, [(a["imported_eq"], 12000, 0), (cash, 0, 12000)], "excavator")
    _je(db, d, [(a["1500"], 4000, 0), (cash, 0, 4000)], "equipment")
    _je(db, d, [(a["1600"], 9000, 0), (cash, 0, 9000)], "vehicle")
    _je(db, d, [(a["1700"], 700, 0), (cash, 0, 700)], "deposit on lease")
    _je(db, d, [(a["tools"], 1500, 0), (cash, 0, 1500)], "tools")
    _je(db, d, [(a["deposits"], 2500, 0), (cash, 0, 2500)], "deposits")
    _je(db, d, [(a["1400"], 600, 0), (cash, 0, 600)], "prepaid")
    _je(db, d, [(a["shelving"], 1500, 0), (cash, 0, 1500)], "shelving")
    _je(db, d, [(a["6000"], 250, 0), (a["interest"], 0, 250)], "interest accrued")
    _je(db, d, [(a["6000"], 3000, 0), (a["1510"], 0, 3000)], "depreciation")
    _je(db, d, [(a["6000"], 800, 0), (a["accrued"], 0, 800)], "wages accrued")
    _je(db, d, [(cash, 20000, 0), (a["4000"], 0, 20000)], "sales")
    _je(db, d, [(a["6000"], 1000, 0), (a["2100"], 0, 1000)], "card")
    _je(db, d, [(cash, 300, 0), (a["4900"], 0, 300)], "interest")
    db.commit()
    return a


def _statement(db):
    return cash_flow.statement_of_cash_flows(db, START, END)


def test_an_upgraded_company_sees_the_same_cash_flow(db_session, books, monkeypatch):
    detail = _upgraded(db_session, books.values())
    # the accounts the old rules found by register, number and name
    assert detail[books["register"].id] == "fixed_asset"
    assert detail[books["imported_eq"].id] == "fixed_asset"
    assert detail[books["tools"].id] == "other_asset"
    assert detail[books["deposits"].id] == "other_current_asset"
    assert detail[books["mortgage"].id] == "long_term_liability"
    assert detail[books["2400"].id] == "long_term_liability"
    # their default-typed sub-accounts stay current
    assert detail[books["shelving"].id] == "other_current_asset"
    assert detail[books["interest"].id] == "other_current_liability"

    now = _statement(db_session)
    monkeypatch.setattr(cash_flow, "_place", place_2_26)
    before = _statement(db_session)
    assert now == before

    names = lambda rows: {r["account_name"] for r in rows}  # noqa: E731
    assert {"Shop Truck 2024", "Heavy Equipment", "Equipment", "Tools"} <= names(
        now["investing"]
    )
    assert {"Mortgage on Shop", "Loan Payable"} <= names(now["financing"])
    assert {
        "Equipment Deposits",
        "Accrued Wages",
        "Shop Shelving",
        "Accrued Interest",
    } <= names(now["working_capital"])
    assert any("Accumulated" in r["account_name"] for r in now["adjustments"])


def test_the_detail_type_moves_what_the_number_and_name_got_wrong(db_session, books):
    """The two cases the design note names: a Long Term Liability called
    "Trent's truck" is financing, a Fixed Asset numbered 1450 investing.
    2.26.0 put both in working capital."""
    _upgraded(db_session, books.values())
    truck = Account(
        name="Trent's truck",
        account_type=AccountType.LIABILITY,
        detail_type="long_term_liability",
    )
    db_session.add(truck)
    books["deposits"].detail_type = "fixed_asset"
    db_session.commit()
    _je(db_session, date(2026, 4, 1), [(books["1000"], 5000, 0), (truck, 0, 5000)])
    db_session.commit()

    for acct in (truck, books["deposits"]):
        assert place_2_26(acct, set(), set()) == "working_capital"
    out = _statement(db_session)
    assert "Trent's truck" in {r["account_name"] for r in out["financing"]}
    assert "Equipment Deposits" in {r["account_name"] for r in out["investing"]}
    assert "Equipment Deposits" not in {
        r["account_name"] for r in out["working_capital"]
    }


def test_a_register_account_is_investing_whatever_its_detail_type(db_session, books):
    """A fixed-asset type created after the upgrade does not retype its
    account; the register still says it is a fixed asset."""
    _upgraded(db_session, books.values())
    books["register"].detail_type = "other_current_asset"
    db_session.commit()
    out = _statement(db_session)
    assert "Shop Truck 2024" in {r["account_name"] for r in out["investing"]}
