"""Other Income and Other Expense on the P&L (#390, PR 2).

Below Net Operating Income, by the account's detail type; Net Operating
Income prints only when either has something (the owner's answer 4), so a
company without them sees the P&L it saw. `net_income` is the same figure
(a move between sections, never in or out of the total). The P&L by Class
and by Job grids get the same rows so the three reports tie line for line
(the owner's answer 3).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of

PERIOD = "start_date=2026-01-01&end_date=2026-12-31"


def _je(db, day, lines, class_id=None):
    create_journal_entry(
        db,
        day,
        "je",
        [
            {"account_id": a.id, "debit": Decimal(str(dr)), "credit": Decimal(str(cr))}
            for a, dr, cr in lines
        ],
        class_id=class_id,
    )


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)
    interest = Account(
        account_number="7100",
        name="Interest Expense",
        account_type=AccountType.EXPENSE,
        detail_type="other_expense",
    )
    db_session.add(interest)
    db_session.flush()
    a["7100"] = interest
    cash = a["1000"]
    d = date(2026, 5, 1)
    _je(db_session, d, [(cash, 2000, 0), (a["4000"], 0, 2000)])
    _je(db_session, d, [(cash, 300, 0), (a["4900"], 0, 300)])  # Other Income
    _je(db_session, d, [(a["5000"], 500, 0), (cash, 0, 500)])
    _je(db_session, d, [(a["6000"], 400, 0), (cash, 0, 400)])
    _je(db_session, d, [(interest, 50, 0), (cash, 0, 50)])
    db_session.commit()
    return a


def _pl(client, qs=""):
    r = client.get(f"/api/reports/profit-loss?{PERIOD}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _numbers(lines):
    return [i["account_number"] for i in lines]


def test_other_income_and_expense_sit_below_net_operating_income(client, books):
    d = _pl(client)
    assert _numbers(d["income"]) == ["4000"]
    assert _numbers(d["other_income"]) == ["4900"]
    assert _numbers(d["expenses"]) == ["6000"]
    assert _numbers(d["other_expense"]) == ["7100"]
    assert d["has_other"] is True
    assert d["total_income"] == 2000
    assert d["gross_profit"] == 1500
    assert d["total_expenses"] == 400
    assert d["net_operating_income"] == 1100
    assert d["total_other_income"] == 300
    assert d["total_other_expense"] == 50
    assert d["net_other_income"] == 250
    # the same net income as before the split: every income less every cost
    assert d["net_income"] == 2000 + 300 - 500 - 400 - 50 == 1350
    # and the Balance Sheet's net income line agrees
    bs = client.get("/api/reports/balance-sheet?as_of_date=2026-12-31").json()
    assert bs["equity"][-1]["amount"] == d["net_income"]


def test_the_grids_print_full_paths_where_leaf_names_repeat(
    client, db_session, seed_accounts
):
    """A numbered Travel: Lodging: Hotel and a second, unnumbered Hotel
    under Meals: the by-class grid rows carry the full path, not the bare
    leaf, so the two are told apart, in parent-first order; the CSV prints
    the path too (Trent's #416 review, point 1)."""
    a = dict(seed_accounts)

    def expense(number, name, parent=None):
        acct = Account(
            account_number=number,
            name=name,
            account_type=AccountType.EXPENSE,
            parent_id=parent.id if parent else None,
        )
        db_session.add(acct)
        db_session.flush()
        return acct

    travel = expense("7310", "Travel")
    lodging = expense("7311", "Lodging", travel)
    hotel_numbered = expense("7312", "Hotel", lodging)
    meals = expense(None, "Meals")
    hotel_unnumbered = expense(None, "Hotel", meals)
    d = date(2026, 5, 1)
    _je(db_session, d, [(a["1000"], 3000, 0), (a["4000"], 0, 3000)])
    _je(db_session, d, [(hotel_numbered, 120, 0), (a["1000"], 0, 120)])
    _je(db_session, d, [(hotel_unnumbered, 80, 0), (a["1000"], 0, 80)])
    db_session.commit()

    g = client.get(f"/api/reports/profit-loss-by-class?{PERIOD}").json()
    expenses = g["accounts"]["expenses"]
    paths = [row["account_path"] for row in expenses]
    # the numbered chain first (parent-first order), the unnumbered after
    assert paths == ["Travel: Lodging: Hotel", "Meals: Hotel"]
    labels = [row["account_label"] for row in expenses]
    assert labels[0] == "7312 - Travel: Lodging: Hotel"
    # the leaf name is kept for compatibility
    assert [row["account_name"] for row in expenses] == ["Hotel", "Hotel"]

    rows = rows_of(client.get(f"/api/reports/profit-loss-by-class/csv?{PERIOD}").text)
    csv_paths = [r[2] for r in rows]
    assert "Travel: Lodging: Hotel" in csv_paths
    assert "Meals: Hotel" in csv_paths


def test_per_type_totals_are_kept_across_the_split(client, books):
    """Income and Other Income together are the income type's total as
    2.26 printed it; Expenses and Other Expense, the expense type's."""
    d = _pl(client)
    assert d["total_income"] + d["total_other_income"] == 2300
    assert d["total_expenses"] + d["total_other_expense"] == 450


def test_without_other_accounts_the_pl_is_the_one_it_was(
    client, db_session, seed_accounts, monkeypatch
):
    from app.routes.reports import financial

    a = seed_accounts
    _je(db_session, date(2026, 5, 1), [(a["1000"], 900, 0), (a["4000"], 0, 900)])
    _je(db_session, date(2026, 5, 1), [(a["6000"], 100, 0), (a["1000"], 0, 100)])
    db_session.commit()
    d = _pl(client)
    assert d["has_other"] is False
    assert d["other_income"] == [] and d["other_expense"] == []

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss/pdf?{PERIOD}")
    words = [r["cells"][0].strip() for r in captured["s"][0]["rows"]]
    assert words == [
        "Income",
        "4000 - Service Income",
        "Total Income",
        "Cost of Goods Sold",
        "Total Cost of Goods Sold",
        "Gross Profit",
        "Expenses",
        "6000 - Advertising & Marketing",
        "Total Expenses",
        "Net Income",
    ]
    rows = rows_of(client.get(f"/api/reports/profit-loss/csv?{PERIOD}").text)
    assert not any("Operating" in r[2] or "Other" in r[2] for r in rows)


def test_the_pdf_prints_the_block(client, books, monkeypatch):
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
    words = [r["cells"][0].strip() for r in rows]
    assert words[words.index("Total Expenses") :] == [
        "Total Expenses",
        "Net Operating Income",
        "Other Income",
        "4900 - Other Income",
        "Total Other Income",
        "Other Expense",
        "7100 - Interest Expense",
        "Total Other Expense",
        "Net Other Income",
        "Net Income",
    ]
    cells = {r["cells"][0].strip(): r["cells"][1] for r in rows}
    assert cells["Net Operating Income"] == "$1,100.00"
    assert cells["Net Other Income"] == "$250.00"
    assert cells["Net Income"] == "$1,350.00"


def test_the_csv_prints_the_block(client, books):
    rows = rows_of(client.get(f"/api/reports/profit-loss/csv?{PERIOD}").text)
    by = {r[2]: r for r in rows}
    assert by["Net Operating Income"][3] == "1100.00"
    other = next(r for r in rows if r[1] == "4900")
    assert other[0] == "Other Income" and other[3] == "300.00"
    assert by["Total Other Expense"][0] == "Other Expense"
    assert by["Net Other Income"][3] == "250.00"
    assert by["Net Income"][3] == "1350.00"
    assert [r[2] for r in rows].index("Net Operating Income") < [
        r[2] for r in rows
    ].index("Total Other Income")


def test_the_prior_year_keeps_the_block_when_only_last_year_had_it(
    client, db_session, books
):
    a = books
    _je(db_session, date(2025, 5, 1), [(a["1000"], 40, 0), (a["4900"], 0, 40)])
    db_session.commit()
    d = _pl(client, "&compare=prior_year")
    assert d["prior"]["total_other_income"] == 40
    assert d["prior"]["net_operating_income"] == 0
    other = next(i for i in d["other_income"] if i["account_number"] == "4900")
    assert other["amount"] == 300 and other["prior_amount"] == 40

    # a P&L whose current year has no other accounts still prints the
    # block when the prior year has one
    d = client.get(
        "/api/reports/profit-loss?start_date=2026-06-01&end_date=2026-12-31"
        "&compare=prior_year"
    ).json()
    assert d["has_other"] is False  # neither year has activity
    d = client.get(
        "/api/reports/profit-loss?start_date=2026-05-01&end_date=2026-05-31"
        "&compare=prior_year"
    ).json()
    assert d["has_other"] is True


def test_the_grids_tie_to_the_pl_line_for_line(client, db_session, books):
    from app.models.classes import TxnClass

    shop = TxnClass(name="Shop")
    db_session.add(shop)
    db_session.commit()
    a = books
    _je(
        db_session,
        date(2026, 6, 1),
        [(a["1000"], 25, 0), (a["4900"], 0, 25)],
        class_id=shop.id,
    )
    db_session.commit()
    pl = _pl(client)
    for path in ("profit-loss-by-class", "profit-loss-by-job"):
        g = client.get(f"/api/reports/{path}?{PERIOD}").json()
        assert g["has_other"] is True, path
        assert [x["account_number"] for x in g["accounts"]["other_income"]] == ["4900"]
        assert [x["account_number"] for x in g["accounts"]["other_expense"]] == ["7100"]
        assert [x["account_number"] for x in g["accounts"]["income"]] == ["4000"]
        for grid_key, pl_key in (
            ("total_income", "total_income"),
            ("total_gross_profit", "gross_profit"),
            ("total_expenses", "total_expenses"),
            ("total_net_operating_income", "net_operating_income"),
            ("total_other_income", "total_other_income"),
            ("total_other_expense", "total_other_expense"),
            ("total_net_other_income", "net_other_income"),
            ("total_net_income", "net_income"),
        ):
            assert g[grid_key] == pl[pl_key], (path, grid_key)
    by_class = client.get(f"/api/reports/profit-loss-by-class?{PERIOD}").json()
    col = next(c for c in by_class["classes"] if c["class_name"] == "Shop")
    assert col["other_income"] == 25 and col["income"] == 0
    assert col["net_income"] == col["net_operating_income"] + col["net_other_income"]
    one = _pl(client, f"&class_id={shop.id}")
    assert one["total_other_income"] == 25 and one["has_other"] is True


def test_the_grid_exports_print_the_block(client, books, monkeypatch):
    from app.routes.reports import financial

    rows = rows_of(client.get(f"/api/reports/profit-loss-by-class/csv?{PERIOD}").text)
    labels = [r[2] for r in rows]
    assert labels.index("Total Expenses") < labels.index("Net Operating Income")
    assert labels.index("Net Operating Income") < labels.index("Total Other Income")
    other = next(r for r in rows if r[1] == "4900")
    assert other[0] == "Other Income" and other[-1] == "300.00"
    assert rows[labels.index("Net Other Income")][-1] == "250.00"
    long = rows_of(
        client.get(f"/api/reports/profit-loss-by-job/csv?{PERIOD}&layout=long").text
    )
    assert any(r[0] == "Other Expense" and r[1] == "7100" for r in long)

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss-by-class/pdf?{PERIOD}")
    words = [r["cells"][0].strip() for r in captured["s"][0]["rows"]]
    assert words[words.index("Total Expenses") :] == [
        "Total Expenses",
        "Net Operating Income",
        "Other Income",
        "4900 - Other Income",
        "Total Other Income",
        "Other Expense",
        "7100 - Interest Expense",
        "Total Other Expense",
        "Net Other Income",
        "Net Income",
    ]


def test_the_classes_activity_carries_the_grids_figures(client, books):
    """Operating income and expenses as the grid has them, Other Income and
    Other Expense beside them; the list page adds them for display."""
    d = client.get(f"/api/classes/activity?{PERIOD}").json()
    company = d["company"]
    assert company["income"] == 2000 and company["other_income"] == 300
    assert company["expenses"] == 400 and company["other_expense"] == 50
    assert company["gross_profit"] == company["income"] - company["cogs"]
    for row in d["classes"] + [company, d["totals"]]:
        assert round(
            row["income"]
            - row["cogs"]
            - row["expenses"]
            + row["other_income"]
            - row["other_expense"],
            2,
        ) == round(row["net_income"], 2)
        assert round(row["gross_profit"], 2) == round(row["income"] - row["cogs"], 2)


def test_the_class_page_cards_are_operating_with_a_net_other_income_card():
    """The list rows still fold Other back into income and expenses (one
    net per row), but the class page's cards are operating — so Income −
    Cost of goods = Gross profit — with a Net other income card added only
    when the class has other activity (Trent's #390 review, point 3)."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/classes.js").read_text(
        encoding="utf-8"
    )
    # the list's rows still net four figures
    assert "income: (c.income || 0) + (c.other_income || 0)" in js
    assert "expenses: (c.expenses || 0) + (c.other_expense || 0)" in js
    # the class page cards are operating only, not Income + Other Income
    assert "d.total_income + (d.total_other_income || 0)" not in js
    assert "d.total_expenses + (d.total_other_expense || 0)" not in js
    assert "stat(T('Income'), ClassesPage.money(d.total_income))" in js
    assert "stat('Expenses', ClassesPage.money(d.total_expenses))" in js
    # a Net other income card, gated on the class having other activity
    assert "(d.total_other_income || d.total_other_expense)" in js
    assert "stat('Net other income', ClassesPage.money(d.net_other_income)" in js


def test_a_nonprofit_reads_other_revenue_and_support(
    client, db_session, books, monkeypatch
):
    from app.routes.reports import financial
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss/pdf?{PERIOD}")
    words = [r["cells"][0].strip() for r in captured["s"][0]["rows"]]
    assert "Other Revenue & Support" in words
    assert "Total Other Revenue & Support" in words
    assert "Change in Net Assets from Operations" in words
    assert "Net Operating Income" not in words


def test_a_zero_other_income_account_prints_no_heading_in_any_writer(
    client, db_session, seed_accounts, monkeypatch
):
    """An Other Income account netting to $0 beside an Other Expense with a
    figure: the P&L still carries the $0 account's data (only the heading is
    gated), but no writer — screen flag, PDF, CSV, grid — opens an Other
    Income section (Trent's #390 review, point 2)."""
    from app.routes.reports import financial

    a = dict(seed_accounts)
    interest = Account(
        account_number="7100",
        name="Interest Expense",
        account_type=AccountType.EXPENSE,
        detail_type="other_expense",
    )
    db_session.add(interest)
    db_session.flush()
    d = date(2026, 5, 1)
    _je(db_session, d, [(a["1000"], 2000, 0), (a["4000"], 0, 2000)])
    _je(db_session, d, [(a["1000"], 100, 0), (a["4900"], 0, 100)])  # Other Income
    _je(db_session, d, [(a["4900"], 100, 0), (a["1000"], 0, 100)])  # reversed to $0
    _je(db_session, d, [(interest, 50, 0), (a["1000"], 0, 50)])  # Other Expense
    db_session.commit()

    pl = _pl(client)
    assert pl["has_other"] is True
    assert pl["has_other_income"] is False
    assert pl["has_other_expense"] is True
    # the $0 account's data is still there; only the heading is gated
    assert _numbers(pl["other_income"]) == ["4900"]
    assert pl["total_other_income"] == 0

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss/pdf?{PERIOD}")
    words = [r["cells"][0].strip() for r in captured["s"][0]["rows"]]
    assert "Other Income" not in words and "Total Other Income" not in words
    assert "Other Expense" in words

    rows = rows_of(client.get(f"/api/reports/profit-loss/csv?{PERIOD}").text)
    assert not any(r[2] == "Other Income" for r in rows)
    assert any(r[0] == "Other Expense" for r in rows)

    g = client.get(f"/api/reports/profit-loss-by-class?{PERIOD}").json()
    assert g["accounts"]["other_income"] == []


def test_an_other_account_netting_to_nothing_opens_no_block(
    client, db_session, seed_accounts
):
    """4900 with a posting and its reversal: the P&L and the grids agree
    that there is nothing below Net Operating Income."""
    a = seed_accounts
    d = date(2026, 5, 1)
    _je(db_session, d, [(a["1000"], 900, 0), (a["4000"], 0, 900)])
    _je(db_session, d, [(a["1000"], 60, 0), (a["4900"], 0, 60)])
    _je(db_session, d, [(a["4900"], 60, 0), (a["1000"], 0, 60)])
    db_session.commit()
    pl = _pl(client)
    assert pl["has_other"] is False
    assert pl["total_other_income"] == 0
    for path in ("profit-loss-by-class", "profit-loss-by-job"):
        g = client.get(f"/api/reports/{path}?{PERIOD}").json()
        assert g["has_other"] is False, path
    rows = rows_of(client.get(f"/api/reports/profit-loss/csv?{PERIOD}").text)
    assert not any("Operating" in r[2] for r in rows)
