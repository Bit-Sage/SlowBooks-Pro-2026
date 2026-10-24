"""The Profit & Loss with one column per month or quarter (#478).

`columns=month|quarter` on /reports/profit-loss runs the period once per
column and once for the Total, and lays it out in P&L by Class's grid shape
(`columns`, `accounts` with amounts[], the grid's totals), so its screen,
PDF and CSV are the grid's. Every column ties to the plain P&L for its own
dates and the Total column to the P&L for the whole period; sub-account
families and their totals appear in every column. The books are
test_pl_detail's: March, April and May lines.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of
from tests.test_pl_detail import books_fixture  # noqa: F401

D = Decimal
SPRING = "start_date=2026-03-01&end_date=2026-05-31"
KEYS = {
    "operating_income": "total_operating_income",
    "cogs": "total_cogs",
    "operating_gross_profit": "operating_gross_profit",
    "operating_expenses": "total_operating_expenses",
    "other_income": "total_other_income",
    "net_income": "net_income",
}


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def _pl(client, start, end, qs=""):
    return _get(
        client, f"/api/reports/profit-loss?start_date={start}&end_date={end}{qs}"
    )


@pytest.mark.parametrize("qs", ["", "&exclude_adjusting=true", "&class_id={retail}"])
def test_each_month_is_that_months_p_and_l_and_the_total_is_the_periods(
    client, books, qs
):
    qs = qs.format(retail=books["retail"])
    d = _get(client, f"/api/reports/profit-loss?{SPRING}&columns=month{qs}")
    assert [c["name"] for c in d["columns"]] == ["Mar 2026", "Apr 2026", "May 2026"]
    assert [(c["start_date"], c["end_date"]) for c in d["columns"]] == [
        ("2026-03-01", "2026-03-31"),
        ("2026-04-01", "2026-04-30"),
        ("2026-05-01", "2026-05-31"),
    ]
    for c in d["columns"]:
        plain = _pl(client, c["start_date"], c["end_date"], qs)
        for col_key, pl_key in KEYS.items():
            assert c[col_key] == plain[pl_key], (c["name"], col_key)
    whole = _pl(client, "2026-03-01", "2026-05-31", qs)
    for col_key, pl_key in KEYS.items():
        assert d[f"total_{col_key}"] == whole[pl_key], col_key
        assert d[pl_key] == whole[pl_key]
    # account by account: the columns add up to the account's total
    for rows in d["accounts"].values():
        for a in rows:
            assert sum(D(str(x)) for x in a["amounts"]) == D(str(a["total"]))


def test_march_and_may_have_their_own_lines(client, books):
    d = _get(client, f"/api/reports/profit-loss?{SPRING}&columns=month")
    ads = next(a for a in d["accounts"]["expenses"] if a["account_number"] == "6000")
    assert ads["amounts"] == [999.0, 100.0, 0.0] and ads["total"] == 1099.0
    sales = next(a for a in d["accounts"]["income"] if a["account_number"] == "4000")
    assert sales["amounts"] == [0.0, 1000.0, 888.0]


def test_families_and_their_totals_are_in_every_column(client, books, db_session):
    a = books
    create_journal_entry(
        db_session,
        date(2026, 5, 2),
        "may hotel",
        [
            {"account_id": a["hotel"].id, "debit": D("25"), "credit": D("0")},
            {"account_id": a["1000"].id, "debit": D("0"), "credit": D("25")},
        ],
    )
    db_session.commit()
    d = _get(client, f"/api/reports/profit-loss?{SPRING}&columns=month")
    shape = [
        (r["kind"], r["depth"], r["label"], r.get("amounts"), r.get("total"))
        for r in d["tree"]["expenses"]
    ]
    assert shape == [
        ("account", 0, "6000 - Advertising & Marketing", [999.0, 100.0, 0.0], 1099.0),
        ("heading", 0, "7000 - Travel", None, None),
        ("heading", 1, "7010 - Hotel", None, None),
        ("account", 2, "7011 - Lodging Tax", [0.0, 10.0, 0.0], 10.0),
        ("direct", 2, "Direct postings to Hotel", [0.0, 100.0, 25.0], 125.0),
        ("total", 1, "Total 7010 - Hotel", [0.0, 110.0, 25.0], 135.0),
        ("account", 1, "7020 - Flight", [0.0, 200.0, 0.0], 200.0),
        ("direct", 1, "Direct postings to Travel", [0.0, 50.0, 0.0], 50.0),
        ("total", 0, "Total 7000 - Travel", [0.0, 360.0, 25.0], 385.0),
    ]
    # each column's family total is the plain P&L's for that month
    may = _pl(client, "2026-05-01", "2026-05-31")["tree"]["expenses"]
    assert next(r for r in may if r["label"] == "Total 7000 - Travel")["amount"] == 25.0


def test_quarters_follow_the_fiscal_year(client, books):
    d = _get(client, f"/api/reports/profit-loss?{SPRING}&columns=quarter")
    # the calendar year: Jan–Mar clipped to March, Apr–Jun clipped to May
    assert [c["name"] for c in d["columns"]] == ["Mar 1–31, 2026", "Apr 1–May 31, 2026"]
    whole = _get(
        client,
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31&columns=quarter",
    )
    assert [c["name"] for c in whole["columns"]] == [
        "Jan–Mar 2026", "Apr–Jun 2026", "Jul–Sep 2026", "Oct–Dec 2026",
    ]  # fmt: skip
    r = client.put("/api/settings", json={"fiscal_year_start_month": "2"})
    assert r.status_code == 200, r.text
    fy = _get(
        client,
        "/api/reports/profit-loss?start_date=2026-02-01&end_date=2027-01-31&columns=quarter",
    )
    assert [c["name"] for c in fy["columns"]] == [
        "Feb–Apr 2026", "May–Jul 2026", "Aug–Oct 2026", "Nov–Jan 2027",
    ]  # fmt: skip
    assert fy["columns"][-1]["start_date"] == "2026-11-01"
    assert fy["columns"][-1]["end_date"] == "2027-01-31"


def test_what_is_refused(client, books):
    base = "/api/reports/profit-loss"
    assert client.get(f"{base}?{SPRING}&columns=week").status_code == 422
    both = client.get(f"{base}?{SPRING}&columns=month&compare=prior_year")
    assert both.status_code == 400
    long = client.get(f"{base}?start_date=2000-01-01&end_date=2026-12-31&columns=month")
    assert long.status_code == 400
    # the plain P&L is as it was
    plain = _get(client, f"{base}?{SPRING}")
    assert "columns" not in plain and "columns_by" not in plain


def test_the_csv_is_the_grid_with_its_families(client, books):
    r = client.get(f"/api/reports/profit-loss/csv?{SPRING}&columns=month")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].endswith(
        "profit-loss-by-month_2026-03-01_2026-05-31.csv"
    )
    text = r.text.lstrip("\ufeff")
    assert text.splitlines()[1] == "Report,Profit & Loss by Month"
    rows = rows_of(text)
    assert rows[0] == [
        "Section", "Account number", "Account name",
        "Mar 2026", "Apr 2026", "May 2026", "Total",
    ]  # fmt: skip
    names = [x[2] for x in rows[1:] if x[0] == "Expenses"]
    # a family's rows as the screen prints them, indented two spaces a level
    assert names == [
        "Advertising & Marketing",
        "Travel",
        "  Travel: Hotel",
        "    Travel: Hotel: Lodging Tax",
        "    Direct postings to Hotel",
        "  Total 7010 - Hotel",
        "  Travel: Flight",
        "  Direct postings to Travel",
        "Total 7000 - Travel",
        "Total Expenses",
    ]
    by_name = {x[2].strip(): x for x in rows[1:]}
    assert by_name["Travel"] == ["Expenses", "7000", "Travel", "", "", "", ""]
    assert by_name["Direct postings to Travel"][1] == "7000"
    assert by_name["Total 7000 - Travel"][1:] == [
        "",
        "Total 7000 - Travel",
        "",
        "360.00",
        "",
        "360.00",
    ]
    assert (
        by_name["Net Income"][-1]
        == f"{D(str(_pl(client, '2026-03-01', '2026-05-31')['net_income'])):.2f}"
    )
    # long: one row per account and month, for a pivot table
    long = rows_of(
        client.get(
            f"/api/reports/profit-loss/csv?{SPRING}&columns=month&layout=long"
        ).text.lstrip("\ufeff")
    )
    assert long[0] == ["Section", "Account number", "Account name", "Month", "Amount"]
    assert ["Expenses", "6000", "Advertising & Marketing", "Mar 2026", "999.00"] in long


def test_the_pdf_is_the_grid(client, books):
    from app.routes.reports.financial import _grid_sections, _pl_columns_title
    from app.services.terminology import Terms

    r = client.get(f"/api/reports/profit-loss/pdf?{SPRING}&columns=quarter")
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert "profit-loss-by-quarter_" in r.headers["content-disposition"]
    d = _get(client, f"/api/reports/profit-loss?{SPRING}&columns=month")
    t = Terms()
    (s,) = _grid_sections(d, _pl_columns_title(d, t), d["columns"], t)
    assert s["title"] == "Profit & Loss by Month"
    assert s["columns"] == ["Account", "Mar 2026", "Apr 2026", "May 2026", "Total"]
    cells = {row["cells"][0].strip(): row for row in s["rows"]}
    assert cells["Total 7000 - Travel"]["style"] == "subtotal"
    assert cells["Total 7000 - Travel"]["indent"] == 1
    assert cells["7011 - Lodging Tax"]["indent"] == 3


def test_a_saved_p_and_l_keeps_its_columns(client, books):
    r = client.post(
        "/api/saved-reports",
        json={
            "name": "Spring by month",
            "report_type": "profit_loss",
            "parameters": {
                "start_date": "2026-03-01",
                "end_date": "2026-05-31",
                "columns": "month",
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    saved = next(
        s for s in _get(client, "/api/saved-reports") if s["name"] == "Spring by month"
    )
    assert saved["parameters"]["columns"] == "month"
