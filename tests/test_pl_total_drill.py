"""The P&L's totals open the transactions behind them (#479).

The drill-down took one account_id, so Total Income, Gross Profit, Total
Expenses and Net Income were not links, nor was a sub-account family's
total. Now /reports/account-transactions also takes `section` (one of the
P&L's totals) or `family` (a parent account and its sub-accounts): every
line of the set, each with its account, signed toward the total, with a
running total that ends on the statement's figure, for the same dates,
class, job and adjusting-entries choice. The books are test_pl_detail's.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.ledger_exports import rows_of
from tests.test_pl_detail import PERIOD, books_fixture  # noqa: F401

D = Decimal
DRILL = "/api/reports/account-transactions"
SECTIONS = {
    "income": "total_operating_income",
    "cogs": "total_cogs",
    "expenses": "total_operating_expenses",
    "other_income": "total_other_income",
    "other_expense": "total_other_expense",
    "gross_profit": "operating_gross_profit",
    "net_operating_income": "net_operating_income",
    "net_other_income": "net_other_income",
    "net_income": "net_income",
}


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "qs",
    [
        "",
        "&exclude_adjusting=true",
        "&class_id={retail}",
        "&job_id={kitchen}",
        "&job_id=0",
    ],
    ids=["all", "no-adjusting", "one-class", "one-job", "no-job"],
)
def test_every_total_is_the_statements_figure(client, books, qs):
    qs = qs.format(retail=books["retail"], kitchen=books["kitchen"])
    if "job_id" in qs:
        # the P&L has no job: P&L Detail (#477) is the P&L for a job
        pl = _get(client, f"/api/reports/profit-loss-detail?{PERIOD}{qs}")
    else:
        pl = _get(client, f"/api/reports/profit-loss?{PERIOD}{qs}")
    for section, key in SECTIONS.items():
        d = _get(client, f"{DRILL}?{PERIOD}&section={section}{qs}")
        assert d["period_net"] == pl[key], section
        if d["entries"]:
            assert d["entries"][-1]["running_balance"] == pl[key], section
        assert sum(D(str(e["amount"])) for e in d["entries"]) == D(str(pl[key]))


def test_the_lines_say_their_account_and_costs_count_against_net_income(client, books):
    d = _get(client, f"{DRILL}?{PERIOD}&section=net_income")
    assert d["set"] == {
        "kind": "section",
        "section": "net_income",
        "family": None,
        "label": "Net Income",
        "total_key": "net_income",
    }
    assert d["account"] is None and d["opening_balance"] == 0
    dates = [e["date"] for e in d["entries"]]
    assert dates == sorted(dates)
    hotel = next(
        e for e in d["entries"] if e["account_label"] == "7010 - Travel: Hotel"
    )
    assert hotel["amount"] == -100.0 and hotel["debit"] == 100.0
    sale = next(e for e in d["entries"] if e["num"] == "R-1")
    assert sale["account_label"] == "4000 - Service Income" and sale["amount"] == 500.0
    # every P&L account with a line is counted, and no other
    assert {e["account_id"] for e in d["entries"]} == set(d["account_ids"])
    assert books["1000"].id not in d["account_ids"]


def test_a_family_is_the_parent_and_everything_beneath_it(client, books):
    d = _get(client, f"{DRILL}?{PERIOD}&family={books['travel'].id}")
    assert d["set"]["label"] == "Total 7000 - Travel"
    assert d["period_net"] == 360.0
    assert set(d["account_ids"]) == {
        books[k].id for k in ("travel", "hotel", "lodging", "flight")
    }
    tree = _get(client, f"/api/reports/profit-loss?{PERIOD}")["tree"]["expenses"]
    total = next(r for r in tree if r["label"] == "Total 7000 - Travel")
    assert total["amount"] == d["period_net"]
    assert total["account_id"] == books["travel"].id  # what the screen's link sends
    # a family one level down
    hotel = _get(client, f"{DRILL}?{PERIOD}&family={books['hotel'].id}")
    assert hotel["period_net"] == 110.0
    # with a class: nothing of Travel is in Retail
    retail = _get(
        client,
        f"{DRILL}?{PERIOD}&family={books['travel'].id}&class_id={books['retail']}",
    )
    assert retail["entries"] == [] and retail["period_net"] == 0


def test_a_by_job_columns_subtotal_ties(client, books):
    job = books["kitchen"]
    grid = _get(client, f"/api/reports/profit-loss-by-job?{PERIOD}")
    col = next(c for c in grid["jobs"] if c["job_id"] == job)
    for section, key in (
        ("income", "operating_income"),
        ("gross_profit", "operating_gross_profit"),
        ("net_income", "net_income"),
    ):
        d = _get(client, f"{DRILL}?{PERIOD}&section={section}&job_id={job}")
        assert d["period_net"] == col[key], section
        assert d["job_name"] == "Kitchen"
    # the Total column: the company's
    d = _get(client, f"{DRILL}?{PERIOD}&section=net_income")
    assert d["period_net"] == grid["total_net_income"]


def test_what_is_asked_must_be_one_set(client, books):
    assert client.get(f"{DRILL}?{PERIOD}").status_code == 400
    both = client.get(f"{DRILL}?{PERIOD}&account_id={books['4000'].id}&section=income")
    assert both.status_code == 400
    assert client.get(f"{DRILL}?{PERIOD}&section=assets").status_code == 422
    assert client.get(f"{DRILL}?{PERIOD}&family=999999").status_code == 404
    bank = client.get(f"{DRILL}?{PERIOD}&family={books['1000'].id}")
    assert bank.status_code == 400
    # one account still works as it did
    one = _get(client, f"{DRILL}?{PERIOD}&account_id={books['4000'].id}")
    assert one["account"]["number"] == "4000" and "set" not in one


def test_the_csv_and_pdf_of_a_total(client, books):
    r = client.get(f"{DRILL}/csv?{PERIOD}&section=expenses&exclude_adjusting=true")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].endswith(
        "AccountDetail_total-expenses_2026-04-01_2026-04-30.csv"
    )
    text = r.text.lstrip("﻿")
    assert text.splitlines()[1] == "Report,Account detail — Total Expenses"
    assert "adjusting entries left out" in text.splitlines()[2]
    rows = rows_of(text)
    assert rows[0] == [
        "Date", "Account", "Type", "Num", "Name", "Memo",
        "Debit", "Credit", "Amount", "Running total",
    ]  # fmt: skip
    lines, total = rows[1:-1], rows[-1]
    assert total[5] == "Total Expenses" and total[8] == "400.00"
    assert sum(D(x[8]) for x in lines) == D("400.00")
    assert lines[-1][9] == "400.00"
    pdf = client.get(f"{DRILL}/pdf?{PERIOD}&family={books['travel'].id}")
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:4] == b"%PDF"
    assert "AccountDetail_total-7000-travel_" in pdf.headers["content-disposition"]


def test_the_pdf_section_of_a_family(client, books):
    from app.routes.reports.financial import _account_transactions_section
    from app.services.terminology import Terms

    d = _get(client, f"{DRILL}?{PERIOD}&family={books['travel'].id}")
    s = _account_transactions_section(d, Terms())
    assert s["title"] == "Account detail — Total 7000 - Travel"
    assert s["columns"] == ["Date", "Account", "Name", "Memo", "Amount", "Running"]
    assert s["rows"][-1]["cells"][3:5] == ["Total 7000 - Travel", "$360.00"]


def test_the_screens_totals_are_links():
    js = Path("app/static/js/reports.js").read_text(encoding="utf-8")
    assert "openSetDrillDown" in js
    # the P&L, one class's P&L and the grids wire their totals
    assert js.count("ReportsPage._setLink(") >= 4
    assert "total: (key) =>" in js
    # the drill-down's address opens the set view for section= or family=
    assert "(p.section || p.family)" in js
