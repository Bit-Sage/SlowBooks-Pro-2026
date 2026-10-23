"""The account detail types migration (#390, 4ac01efbd72a) on a 2.26 chart.

The backfill must leave every account where the cash flow statement puts
it today: a register-linked fixed asset, imported equipment and an
imported mortgage stay non-current, whatever their numbers. Runs on SQLite
always, and on PostgreSQL when SLOWBOOKS_TEST_POSTGRES_URL names a server
the test may create a scratch database on.
"""

import os
import shutil
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "d3e4f5a6b7c8"
REVISION = "4ac01efbd72a"

# (key, number, name, type as stored, bank_kind, parent key)
CHART = [
    ("checking", "1000", "Checking", "ASSET", "bank", None),
    ("ar", "1100", "Accounts Receivable", "ASSET", None, None),
    ("undeposited", "1200", "Undeposited Funds", "ASSET", None, None),
    ("prepaid", "1400", "Prepaid Expenses", "ASSET", None, None),
    ("equipment", "1500", "Equipment", "ASSET", None, None),
    ("accum", "1510", "Accumulated Depreciation", "ASSET", None, None),
    ("vehicles", "1600", "Vehicles", "ASSET", None, None),
    ("other_assets", "1700", "Other Assets", "ASSET", None, None),
    # the seeded 1500 renamed: still investing by its number
    ("tools", "1550", "Tools", "ASSET", None, None),
    # a numbered current asset named like equipment stays current (as now)
    ("deposits", "1450", "Equipment Deposits", "ASSET", None, None),
    # register-linked, numbered and named like nothing in particular
    ("register", "1450.1", "Shop Truck 2024", "ASSET", None, None),
    # imported, unnumbered, named like equipment
    ("imported_eq", None, "Heavy Equipment", "ASSET", None, None),
    ("ap", "2000", "Accounts Payable", "LIABILITY", None, None),
    ("card", "2100", "Credit Card", "LIABILITY", "credit_card", None),
    ("sales_tax", "2200", "Sales Tax Payable", "LIABILITY", None, None),
    ("loan", "2400", "Loan Payable", "LIABILITY", None, None),
    ("other_cl", "2500", "Other Current Liabilities", "LIABILITY", None, None),
    # imported, unnumbered
    ("mortgage", None, "Mortgage on Shop", "LIABILITY", None, None),
    ("equity", "3000", "Owner's Equity", "EQUITY", None, None),
    ("income", "4000", "Service Income", "INCOME", None, None),
    ("other_income", "4900", "Other Income", "INCOME", None, None),
    ("renamed_4900", "4950", "Other Income", "INCOME", None, None),
    ("interest", "4910", "Interest Income", "INCOME", None, "other_income"),
    ("cogs", "5000", "Cost of Goods Sold", "COGS", None, None),
    ("expense", "6000", "Advertising & Marketing", "EXPENSE", None, None),
    ("fx", "6999", "Exchange Gain/Loss", "EXPENSE", None, None),
    ("disposal", "7999", "Gain/Loss on Asset Disposal", "EXPENSE", None, None),
    # a folder of equipment and what sits in it
    ("fixed_folder", None, "Fixed Assets", "ASSET", None, None),
    ("trailer", None, "Trailer", "ASSET", None, "fixed_folder"),
    # default-typed sub-accounts of non-current parents: still current, as
    # the 2.26 cash flow placed them (no figure moves, #390 review)
    ("shelving", None, "Shop Shelving", "ASSET", None, "equipment"),
    ("accrued", None, "Accrued Interest", "LIABILITY", None, "loan"),
    # a default-typed sub-account of a current parent shares its type
    ("prepaid_rent", None, "Prepaid Rent", "ASSET", None, "prepaid"),
    # a bank account in a folder keeps Bank
    ("bank_folder", None, "Bank Accounts", "ASSET", None, None),
    ("savings", None, "Money Market", "ASSET", "bank", "bank_folder"),
    # a child whose base type differs from its parent's (nothing stopped it)
    ("odd_child", None, "Misfiled Fee", "EXPENSE", None, "income"),
]

EXPECTED = {
    "checking": "bank",
    "ar": "accounts_receivable",
    "undeposited": "other_current_asset",
    "prepaid": "other_current_asset",
    "equipment": "fixed_asset",
    "accum": "fixed_asset",
    "vehicles": "fixed_asset",
    "other_assets": "other_asset",
    "tools": "other_asset",
    "deposits": "other_current_asset",
    "register": "fixed_asset",
    "imported_eq": "fixed_asset",
    "ap": "accounts_payable",
    "card": "credit_card",
    "sales_tax": "other_current_liability",
    "loan": "long_term_liability",
    "other_cl": "other_current_liability",
    "mortgage": "long_term_liability",
    "equity": "equity",
    "income": "income",
    "other_income": "other_income",
    "renamed_4900": "income",
    "interest": "other_income",
    "cogs": "cost_of_goods_sold",
    "expense": "expense",
    "fx": "other_expense",
    "disposal": "other_expense",
    "fixed_folder": "fixed_asset",
    "trailer": "other_current_asset",
    "shelving": "other_current_asset",
    "accrued": "other_current_liability",
    "prepaid_rent": "other_current_asset",
    "bank_folder": "other_current_asset",
    "savings": "bank",
    "odd_child": "expense",
}


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


_TEMPLATE = {}


@pytest.fixture(scope="module", autouse=True)
def _books_at_the_release_before(tmp_path_factory):
    path = tmp_path_factory.mktemp("before") / "before.db"
    command.upgrade(_cfg("sqlite:///" + path.as_posix()), BEFORE)
    _TEMPLATE["sqlite"] = path
    yield
    _TEMPLATE.clear()


def _old_books(url: str) -> dict:
    """A 2.26 chart; returns {key: id}."""
    if url.startswith("sqlite:///"):
        shutil.copyfile(_TEMPLATE["sqlite"], url[len("sqlite:///") :])
    else:
        command.upgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    ids = {}
    try:
        with engine.begin() as conn:
            for key, number, name, atype, kind, parent in CHART:
                conn.execute(
                    sa.text(
                        "INSERT INTO accounts (name, account_number, account_type, "
                        "bank_kind, parent_id, is_active, is_system, balance) VALUES "
                        "(:name, :number, :atype, :kind, :parent, :t, :f, 0)"
                    ),
                    {
                        "name": name,
                        "number": number,
                        "atype": atype,
                        "kind": kind,
                        "parent": ids[parent] if parent else None,
                        "t": True,
                        "f": False,
                    },
                )
                ids[key] = conn.execute(
                    sa.text("SELECT max(id) FROM accounts")
                ).scalar()
            conn.execute(
                sa.text(
                    "INSERT INTO fixed_asset_types (name, asset_account_id, "
                    "accumulated_depreciation_account_id, depreciation_method, "
                    "is_active) VALUES ('Trucks', :a, :b, 'STRAIGHT_LINE', :t)"
                ),
                {"a": ids["register"], "b": ids["accum"], "t": True},
            )
    finally:
        engine.dispose()
    return ids


def _detail_types(url: str) -> dict:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return dict(
                conn.execute(sa.text("SELECT id, detail_type FROM accounts")).all()
            )
    finally:
        engine.dispose()


def _scenario(url: str) -> None:
    ids = _old_books(url)
    command.upgrade(_cfg(url), REVISION)
    got = _detail_types(url)
    assert {k: got[i] for k, i in ids.items()} == EXPECTED

    engine = sa.create_engine(url)
    try:
        cols = {c["name"]: c for c in sa.inspect(engine).get_columns("accounts")}
        assert cols["detail_type"]["nullable"] is False
    finally:
        engine.dispose()

    command.downgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    try:
        cols = {c["name"] for c in sa.inspect(engine).get_columns("accounts")}
        assert "detail_type" not in cols
    finally:
        engine.dispose()


def test_the_backfill_on_sqlite(tmp_path):
    _scenario("sqlite:///" + (tmp_path / "old.db").as_posix())


@pytest.fixture
def postgres_url():
    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_detail_types_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on the machine
        admin.dispose()
        pytest.skip(f"cannot create a scratch PostgreSQL database: {exc}")
    url = sa.engine.make_url(admin_url).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_backfill_on_postgresql(postgres_url):
    _scenario(postgres_url)


def _raw_insert_without_detail_type(url: str) -> None:
    """A raw SQL insert that omits detail_type must not fail the NOT NULL:
    the migration gives the column a server_default, so another tool or a
    manual fix can add a row and get a value rather than an error."""
    command.upgrade(_cfg(url), REVISION)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO accounts (name, account_number, account_type, "
                    "is_active, is_system, balance) VALUES "
                    "('Raw row', '9001', 'EXPENSE', :t, :f, 0)"
                ),
                {"t": True, "f": False},
            )
            got = conn.execute(
                sa.text(
                    "SELECT detail_type FROM accounts WHERE account_number = '9001'"
                )
            ).scalar()
        assert got == "other_current_asset"
    finally:
        engine.dispose()


def test_a_raw_insert_without_detail_type_gets_the_server_default_on_sqlite(tmp_path):
    _raw_insert_without_detail_type("sqlite:///" + (tmp_path / "raw.db").as_posix())


def test_a_raw_insert_without_detail_type_gets_the_server_default_on_postgresql(
    postgres_url,
):
    _raw_insert_without_detail_type(postgres_url)


def _migration():
    from tests.cash_flow_2_26 import migration

    return migration()


def test_the_backfill_and_the_cash_flow_share_the_accumulated_rule():
    """The migration keeps its own copy of the name rules so it never
    changes. The cash flow statement reads the detail type now (#390), and
    keeps one of them: accumulated depreciation, which is a Fixed Asset like
    the asset it wears down, is found by the register and by name."""
    from app.services import cash_flow

    mod = _migration()
    assert mod._ACCUMULATED.pattern == cash_flow._ACCUMULATED.pattern
    assert not hasattr(cash_flow, "_FIXED_ASSET")
    assert not hasattr(cash_flow, "_LONG_TERM_DEBT")


def test_the_backfill_places_every_account_where_the_cash_flow_did():
    """Working capital, investing, financing or an adjustment: for every
    account in the 2.26 chart above, the cash flow statement reading the
    backfilled detail type places it where 2.26.0's number and name rules
    did: no account moves, sub-accounts of non-current parents included."""
    from app.models.accounts import Account, AccountType
    from app.services.cash_flow import _place
    from tests.cash_flow_2_26 import place_2_26

    mod = _migration()
    rows, by_key = [], {}
    for n, (key, number, name, atype, kind, parent) in enumerate(CHART, start=1):
        by_key[key] = n
        rows.append(
            {
                "id": n,
                "name": name,
                "account_number": number,
                "account_type": atype,
                "bank_kind": kind,
                "parent_id": by_key.get(parent),
            }
        )
    fixed, accumulated = {by_key["register"]}, {by_key["accum"]}
    result, _ = mod.backfill(rows, fixed, accumulated)
    for r in rows:
        if r["bank_kind"] == "bank" or r["account_type"] not in (
            "ASSET",
            "LIABILITY",
        ):
            continue
        acct = Account(
            id=r["id"],
            name=r["name"],
            account_number=r["account_number"],
            account_type=AccountType[r["account_type"]],
            bank_kind=r["bank_kind"],
        )
        before = place_2_26(acct, fixed, accumulated)
        acct.detail_type = result[r["id"]]
        now = _place(acct, fixed, accumulated)
        assert now == before, (r["name"], result[r["id"]], before, now)
