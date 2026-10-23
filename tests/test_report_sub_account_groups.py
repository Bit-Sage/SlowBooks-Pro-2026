"""Sub-accounts beneath their parents on the P&L and the Statement of
Activities (#416).

Chris Cree's imported chart has Hotel under Travel; the P&L printed
"Travel: Hotel" flat and the Statement of Activities a bare "Hotel". Now
both print a parent's family together, on the screen (the API's `tree`)
and in the PDF: children indented beneath the parent, the parent's own
postings apart from the family's total, a parent heading when only its
children have activity, every line once. The CSV stays flat, with full
paths. Every total is the flat one.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of

PERIOD = "start_date=2026-01-01&end_date=2026-12-31"


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


def _add(db, number, name, atype, parent=None):
    acct = Account(
        account_number=number,
        name=name,
        account_type=atype,
        parent_id=parent.id if parent else None,
    )
    db.add(acct)
    db.flush()
    return acct


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)
    db = db_session
    E = AccountType.EXPENSE
    a["travel"] = _add(db, "7000", "Travel", E)
    a["hotel"] = _add(db, "7010", "Hotel", E, a["travel"])
    a["hotel_tax"] = _add(db, "7011", "Lodging Tax", E, a["hotel"])
    a["flight"] = _add(db, "7020", "Flight", E, a["travel"])
    # a parent with no postings of its own
    a["vehicle"] = _add(db, "7100", "Vehicle", E)
    a["fuel"] = _add(db, "7110", "Fuel", E, a["vehicle"])
    a["parking"] = _add(db, "7120", "Parking", E, a["vehicle"])  # no activity
    cash = a["1000"]
    d = date(2026, 4, 1)
    _je(db, d, [(cash, 5000, 0), (a["4000"], 0, 5000)])
    _je(db, d, [(a["travel"], 50, 0), (cash, 0, 50)])
    _je(db, d, [(a["hotel"], 100, 0), (cash, 0, 100)])
    _je(db, d, [(a["hotel_tax"], 10, 0), (cash, 0, 10)])
    _je(db, d, [(a["flight"], 200, 0), (cash, 0, 200)])
    _je(db, d, [(a["fuel"], 30, 0), (cash, 0, 30)])
    _je(db, d, [(a["6000"], 400, 0), (cash, 0, 400)])
    db.commit()
    return a


def _pl(client, qs=""):
    r = client.get(f"/api/reports/profit-loss?{PERIOD}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _shape(rows):
    return [(r["kind"], r["depth"], r["label"], r.get("amount")) for r in rows]


def _flat_total_holds(rows, total, key="amount"):
    """Every line printed once: the account and direct rows add to the
    section's total, and so do the top level's lines and family totals."""
    lines = sum(
        Decimal(str(r[key])) for r in rows if r["kind"] in ("account", "direct")
    )
    top = sum(
        Decimal(str(r[key]))
        for r in rows
        if r["depth"] == 0 and r["kind"] in ("account", "total")
    )
    assert lines == top == Decimal(str(total)).quantize(Decimal("0.01"))


def test_the_pl_prints_families_beneath_their_parents(client, books):
    d = _pl(client)
    assert _shape(d["tree"]["expenses"]) == [
        ("account", 0, "6000 - Advertising & Marketing", 400.0),
        ("heading", 0, "7000 - Travel", None),
        ("heading", 1, "7010 - Hotel", None),
        ("account", 2, "7011 - Lodging Tax", 10.0),
        ("direct", 2, "Direct postings to Hotel", 100.0),
        ("total", 1, "Total 7010 - Hotel", 110.0),
        ("account", 1, "7020 - Flight", 200.0),
        ("direct", 1, "Direct postings to Travel", 50.0),
        ("total", 0, "Total 7000 - Travel", 360.0),
        # a parent with no postings of its own still heads its family
        ("heading", 0, "7100 - Vehicle", None),
        ("account", 1, "7110 - Fuel", 30.0),
        ("total", 0, "Total 7100 - Vehicle", 30.0),
    ]
    # the direct row is the parent's own account, for the drill-down
    direct = next(r for r in d["tree"]["expenses"] if r["kind"] == "direct")
    assert direct["account_id"] == books["hotel"].id


def test_no_total_moves_and_nothing_is_counted_twice(client, books):
    d = _pl(client)
    assert d["total_expenses"] == 400 + 50 + 100 + 10 + 200 + 30
    assert d["net_income"] == 5000 - 790
    for key, total in (
        ("expenses", "total_expenses"),
        ("income", "total_income"),
        ("cogs", "total_cogs"),
    ):
        _flat_total_holds(d["tree"][key], d[total])
    # the flat lists are still there, every line once, with full paths
    paths = {i["account_path"] for i in d["expenses"]}
    assert "Travel: Hotel: Lodging Tax" in paths
    assert len(d["expenses"]) == 6


def test_the_prior_year_columns_add_up_by_family(client, db_session, books):
    a = books
    _je(db_session, date(2025, 4, 1), [(a["flight"], 70, 0), (a["1000"], 0, 70)])
    _je(db_session, date(2025, 4, 1), [(a["parking"], 5, 0), (a["1000"], 0, 5)])
    db_session.commit()
    d = _pl(client, "&compare=prior_year")
    rows = d["tree"]["expenses"]
    travel = next(r for r in rows if r["label"] == "Total 7000 - Travel")
    assert travel["amount"] == 360 and travel["prior_amount"] == 70
    vehicle = next(r for r in rows if r["label"] == "Total 7100 - Vehicle")
    assert vehicle["amount"] == 30 and vehicle["prior_amount"] == 5
    parking = next(r for r in rows if r["label"] == "7120 - Parking")
    assert parking["depth"] == 1 and parking["amount"] == 0
    _flat_total_holds(rows, d["total_expenses"])
    _flat_total_holds(rows, d["prior"]["total_expenses"], "prior_amount")


def test_the_pdf_indents_the_families(client, books, monkeypatch):
    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss/pdf?{PERIOD}")
    rows = captured["s"][0]["rows"]
    by = {r["cells"][0].strip(): r for r in rows}
    assert (
        by["7000 - Travel"]["style"] == "group" and by["7000 - Travel"]["indent"] == 1
    )
    assert by["7011 - Lodging Tax"]["indent"] == 3
    assert by["Direct postings to Travel"]["cells"][1] == "$50.00"
    assert by["Total 7000 - Travel"]["style"] == "subtotal"
    assert by["Total 7000 - Travel"]["cells"][1] == "$360.00"
    assert by["Total Expenses"]["cells"][1] == "$790.00"
    assert "7000 - Travel: Hotel" not in by  # the path is the indent now


def test_the_csv_stays_flat_with_full_paths(client, books):
    rows = rows_of(client.get(f"/api/reports/profit-loss/csv?{PERIOD}").text)
    names = [r[2] for r in rows if r[0] == "Expenses"]
    assert "Travel: Hotel: Lodging Tax" in names
    assert "Travel" in names and "Total Expenses" in names
    assert not any(n.startswith("Total Travel") or "Direct" in n for n in names)
    total = sum(
        Decimal(r[3]) for r in rows if r[0] == "Expenses" and r[2] != "Total Expenses"
    )
    assert total == Decimal("790.00")


def test_a_parent_of_another_section_does_not_gather_the_line(
    client, db_session, books
):
    """An expense filed under an income account (nothing stopped it before
    the detail types) prints in Expenses, at the top, with its full path."""
    a = books
    odd = _add(db_session, "7900", "Misfiled Fee", AccountType.EXPENSE, a["4000"])
    _je(db_session, date(2026, 5, 1), [(odd, 15, 0), (a["1000"], 0, 15)])
    db_session.commit()
    d = _pl(client)
    row = next(r for r in d["tree"]["expenses"] if r.get("account_id") == odd.id)
    assert row["kind"] == "account" and row["depth"] == 0
    assert row["label"] == "7900 - Service Income: Misfiled Fee"
    assert not any(r["kind"] == "heading" for r in d["tree"]["income"])
    _flat_total_holds(d["tree"]["expenses"], d["total_expenses"])


def test_a_parent_loop_or_a_deep_chain_cannot_hang_the_report(
    client, db_session, books
):
    """Bad data already saved: A under B under A, and a chain deeper than
    any chart. Each line still prints once and the totals hold."""
    a = books
    E = AccountType.EXPENSE
    x = _add(db_session, "7500", "Loop A", E)
    y = _add(db_session, "7510", "Loop B", E, x)
    db_session.flush()
    x.parent_id = y.id
    chain = [_add(db_session, "8000", "Deep 0", E)]
    for n in range(1, 80):
        chain.append(_add(db_session, f"8{n:03d}", f"Deep {n}", E, chain[-1]))
    _je(db_session, date(2026, 6, 1), [(x, 11, 0), (a["1000"], 0, 11)])
    _je(db_session, date(2026, 6, 1), [(y, 13, 0), (a["1000"], 0, 13)])
    _je(db_session, date(2026, 6, 1), [(chain[-1], 7, 0), (a["1000"], 0, 7)])
    _je(db_session, date(2026, 6, 1), [(chain[0], 3, 0), (a["1000"], 0, 3)])
    db_session.commit()

    d = _pl(client)
    rows = d["tree"]["expenses"]
    printed = [r["account_id"] for r in rows if r["kind"] in ("account", "direct")]
    assert sorted(printed) == sorted(i["account_id"] for i in d["expenses"])
    _flat_total_holds(rows, d["total_expenses"])
    assert d["total_expenses"] == 790 + 11 + 13 + 7 + 3


@pytest.fixture
def nonprofit(db_session, books):
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    return books


def test_the_statement_of_activities_groups_too(client, nonprofit):
    d = client.get(f"/api/reports/statement-of-activities?{PERIOD}").json()
    shape = [
        (r["kind"], r["depth"], r["label"], r.get("total"))
        for r in d["tree"]["expenses"]
    ]
    assert ("heading", 0, "Travel", None) in shape  # a heading has no figures
    assert [(k, depth, label) for k, depth, label, _t in shape if k == "heading"] == [
        ("heading", 0, "Travel"),
        ("heading", 1, "Hotel"),
        ("heading", 0, "Vehicle"),
    ]
    totals = {label: t for k, _d, label, t in shape if k == "total"}
    assert totals == {
        "Total Hotel": 110.0,
        "Total Travel": 360.0,
        "Total Vehicle": 30.0,
    }
    assert ("direct", 1, "Direct postings to Travel", 50.0) in shape
    _flat_total_holds(d["tree"]["expenses"], d["totals"]["expenses"], "total")
    _flat_total_holds(d["tree"]["revenue"], d["totals"]["revenue"], "total")


def test_the_statement_of_activities_pdf_and_csv(client, nonprofit, monkeypatch):
    from app.routes.reports import nonprofit as routes

    captured = {}
    monkeypatch.setattr(
        routes,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/statement-of-activities/pdf?{PERIOD}")
    rows = captured["s"][0]["rows"]
    by = {r["cells"][0].strip(): r for r in rows}
    assert by["Travel"]["style"] == "group" and by["Travel"]["indent"] == 1
    assert by["Lodging Tax"]["indent"] == 3
    assert by["Total Travel"]["cells"][3] == "$360.00"
    assert by["Total Expenses"]["cells"][3] == "$790.00"

    text = client.get(f"/api/reports/statement-of-activities/csv?{PERIOD}").text
    assert "  Travel: Hotel: Lodging Tax,10.00" in text
    assert "Total Travel" not in text


def test_the_statement_of_activities_compares_by_family(client, db_session, nonprofit):
    a = nonprofit
    _je(db_session, date(2025, 4, 1), [(a["fuel"], 9, 0), (a["1000"], 0, 9)])
    db_session.commit()
    d = client.get(
        f"/api/reports/statement-of-activities?{PERIOD}&compare=prior_year"
    ).json()
    vehicle = next(r for r in d["tree"]["expenses"] if r["label"] == "Total Vehicle")
    assert vehicle["total"] == 30 and vehicle["prior_total"] == 9
    assert vehicle["change"] == 21


def test_the_statements_read_the_chart_once(client, books, db_session):
    """One chart index per statement, handed to every helper (#390 review:
    the compare P&L read the whole chart some sixteen times)."""
    from tests.test_no_nplus1_in_list_endpoints import _count_selects

    engine = db_session.get_bind()
    reads = {}
    for name, url in (
        ("pl", f"/api/reports/profit-loss?{PERIOD}&compare=prior_year"),
        ("bs", "/api/reports/balance-sheet?as_of_date=2026-12-31&compare=prior_year"),
    ):
        with _count_selects(engine) as statements:
            assert client.get(url).status_code == 200
        # the chart query: every account, no join to the ledger
        reads[name] = [
            q
            for q in statements
            if "FROM accounts" in q and "transaction" not in q.lower()
        ]
    assert len(reads["pl"]) == 1, reads["pl"]
    assert len(reads["bs"]) == 1, reads["bs"]


def test_both_walkers_share_one_depth_limit():
    """The rows' "Parent: Child" paths and the sub-account tree stop at the
    same MAX_DEPTH, and at a loop, so the two never disagree on bad data."""
    from app.routes.reports import financial
    from app.services import statement_layout

    assert financial.MAX_DEPTH == statement_layout.MAX_DEPTH == 64
    deep = {n: (str(n), f"A{n}", n - 1 if n else None, None, None) for n in range(90)}
    assert len(financial._chain(deep, 89)) == 64
    looped = {1: ("1", "A", 2, None, None), 2: ("2", "B", 1, None, None)}
    assert financial._chain(looped, 1) == [2, 1]
    chart = statement_layout.account_chart(index=looped)
    assert statement_layout.account_path(chart, 1) == "B: A"


def test_the_activities_csv_writes_negatives_as_numbers(client, db_session, nonprofit):
    """Expenses over revenue: the change in net assets is negative, and the
    CSV writes it as a number, not as guarded text ('-...)."""
    import csv
    import io

    a = nonprofit
    _je(db_session, date(2026, 7, 1), [(a["6000"], 9000, 0), (a["1000"], 0, 9000)])
    db_session.commit()
    text = client.get(f"/api/reports/statement-of-activities/csv?{PERIOD}").text
    assert "'-" not in text
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    change = next(r for r in rows if r[0] == "Change in Net Assets")
    assert change[3] == "-4790.00"
