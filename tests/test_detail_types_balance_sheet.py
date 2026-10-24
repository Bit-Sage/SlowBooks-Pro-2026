"""The Balance Sheet groups by detail type (#390, PR 2).

Current / Fixed / Other Assets and Current / Long Term Liabilities, each
with its subtotal, from one helper that feeds the screen (the API's
`groups`), the PDF (`_bs_section`, which the accountant's package reuses)
and the CSV. The flat lists, the prior-year pairing and the drill-down ids
are untouched, and no total moves.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
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
    cash = a["1000"]
    # a long-term note that exists only in 2026
    note = Account(
        account_number="2700",
        name="Note to Bank",
        account_type=AccountType.LIABILITY,
        detail_type="long_term_liability",
    )
    db_session.add(note)
    db_session.flush()
    a["2700"] = note
    _je(db_session, date(2025, 6, 1), [(cash, 10000, 0), (a["2400"], 0, 10000)])
    _je(db_session, date(2025, 6, 1), [(a["1500"], 3000, 0), (cash, 0, 3000)])
    _je(db_session, date(2026, 2, 1), [(cash, 6000, 0), (note, 0, 6000)])
    _je(db_session, date(2026, 3, 1), [(a["1600"], 9000, 0), (cash, 0, 9000)])
    _je(db_session, date(2026, 3, 1), [(a["6000"], 1000, 0), (a["1510"], 0, 1000)])
    _je(db_session, date(2026, 3, 1), [(a["1700"], 500, 0), (cash, 0, 500)])
    _je(db_session, date(2026, 3, 1), [(a["1400"], 250, 0), (cash, 0, 250)])
    _je(db_session, date(2026, 4, 1), [(a["6000"], 400, 0), (a["2100"], 0, 400)])
    _je(db_session, date(2026, 4, 1), [(a["6000"], 75, 0), (a["2500"], 0, 75)])
    _je(db_session, date(2026, 5, 1), [(cash, 20000, 0), (a["4000"], 0, 20000)])
    db_session.commit()
    return a


def _bs(client, qs=""):
    r = client.get(f"/api/reports/balance-sheet?as_of_date={AS_OF}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _shape(groups):
    return [
        (g["heading"], g["total_key"], [i["account_number"] for i in g["lines"]])
        for g in groups
    ]


def test_assets_and_liabilities_print_under_their_groups(client, books):
    d = _bs(client)
    assert _shape(d["groups"]["assets"]) == [
        # Bank first, then Other Current Asset
        ("Current Assets", "total_current_assets", ["1000", "1400"]),
        # accumulated depreciation is a Fixed Asset and nets inside the group
        ("Fixed Assets", "total_fixed_assets", ["1500", "1510", "1600"]),
        ("Other Assets", "total_other_assets", ["1700"]),
    ]
    assert _shape(d["groups"]["liabilities"]) == [
        ("Current Liabilities", "total_current_liabilities", ["2100", "2500"]),
        ("Long Term Liabilities", "total_long_term_liabilities", ["2400", "2700"]),
    ]
    assert d["total_fixed_assets"] == 3000 + 9000 - 1000
    assert d["total_other_assets"] == 500
    assert d["total_current_liabilities"] == 475
    assert d["total_long_term_liabilities"] == 16000
    # every line carries its detail type, the flat lists keep their ids
    assert all(i["detail_type"] and i["account_id"] for i in d["assets"])


def test_the_groups_add_up_to_the_totals_and_no_total_moves(client, books):
    d = _bs(client)
    for side, total in (
        ("assets", "total_assets"),
        ("liabilities", "total_liabilities"),
    ):
        flat = sum(Decimal(str(i["amount"])) for i in d[side])
        grouped = sum(Decimal(str(d[g["total_key"]])) for g in d["groups"][side])
        assert flat == grouped == Decimal(str(d[total])), side
        assert sum(len(g["lines"]) for g in d["groups"][side]) == len(d[side])
    assert round(d["total_assets"], 2) == round(
        d["total_liabilities"] + d["total_equity"], 2
    )


def test_an_empty_group_is_left_out(client, db_session, seed_accounts):
    a = seed_accounts
    _je(db_session, date(2026, 1, 5), [(a["1000"], 100, 0), (a["4000"], 0, 100)])
    db_session.commit()
    d = _bs(client)
    assert [g["heading"] for g in d["groups"]["assets"]] == ["Current Assets"]
    assert d["groups"]["liabilities"] == []
    assert d["total_fixed_assets"] == 0.0


def test_inside_a_group_the_sort_choice_holds(client, books):
    d = _bs(client, "&sort=name")
    fixed = next(g for g in d["groups"]["assets"] if g["heading"] == "Fixed Assets")
    names = [i["account_name"] for i in fixed["lines"]]
    assert names == sorted(names, key=str.lower)


def test_the_prior_year_carries_the_subtotals_and_a_one_year_account(client, books):
    d = _bs(client, "&compare=prior_year")
    p = d["prior"]
    assert p["total_long_term_liabilities"] == 10000
    assert p["total_fixed_assets"] == 3000
    assert p["total_other_assets"] == 0
    lt = next(g for g in d["groups"]["liabilities"] if g["heading"].startswith("Long"))
    note = next(i for i in lt["lines"] if i["account_number"] == "2700")
    assert note["amount"] == 6000 and note["prior_amount"] == 0
    loan = next(i for i in lt["lines"] if i["account_number"] == "2400")
    assert loan["amount"] == loan["prior_amount"] == 10000
    # the current figures are those without the comparison
    plain = _bs(client)
    for key in ("total_assets", "total_fixed_assets", "total_long_term_liabilities"):
        assert d[key] == plain[key]


def test_a_group_with_lines_in_the_prior_year_only_still_prints(
    client, db_session, seed_accounts
):
    a = seed_accounts
    _je(db_session, date(2025, 3, 1), [(a["1700"], 80, 0), (a["1000"], 0, 80)])
    _je(db_session, date(2026, 2, 1), [(a["1000"], 80, 0), (a["1700"], 0, 80)])
    db_session.commit()
    d = _bs(client, "&compare=prior_year")
    other = next(g for g in d["groups"]["assets"] if g["heading"] == "Other Assets")
    assert other["lines"][0]["amount"] == 0 and other["lines"][0]["prior_amount"] == 80
    assert d["prior"]["total_other_assets"] == 80


def test_the_pdf_prints_the_groups_with_their_subtotals(client, books, monkeypatch):
    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?as_of_date={AS_OF}")
    rows = captured["s"][0]["rows"]
    words = [r["cells"][0].strip() for r in rows]
    for heading in ("Current Assets", "Fixed Assets", "Long Term Liabilities"):
        i = words.index(heading)
        assert rows[i]["style"] == "group" and rows[i]["indent"] == 1
        total = rows[words.index(f"Total {heading}")]
        assert total["style"] == "subtotal" and total["indent"] == 1
    fixed = rows[words.index("Total Fixed Assets")]
    assert fixed["cells"][1] == "$11,000.00"
    vehicles = next(r for r in rows if r["cells"][0].strip() == "1600 - Vehicles")
    assert vehicles["indent"] == 2
    assert words.index("Total Fixed Assets") < words.index("Total Assets")


def test_the_csv_section_is_the_group_and_type_the_side(client, books):
    rows = rows_of(
        client.get(f"/api/reports/balance-sheet/csv?as_of_date={AS_OF}").text
    )
    assert rows[0] == ["Section", "Account number", "Account name", "Amount", "Type"]
    vehicles = next(r for r in rows if r[1] == "1600")
    assert vehicles[0] == "Fixed Assets" and vehicles[-1] == "Assets"
    note = next(r for r in rows if r[1] == "2700")
    assert note[0] == "Long Term Liabilities" and note[-1] == "Liabilities"
    sub = next(r for r in rows if r[2] == "Total Long Term Liabilities")
    assert sub[0] == "Long Term Liabilities" and sub[3] == "16000.00"
    total = next(r for r in rows if r[2] == "Total Assets")
    assert total[0] == "Assets"


def test_the_pdf_template_indents_by_level():
    from app.services.pdf_service import _render

    html = _render(
        "report_pdf.html",
        {},
        sections=[
            {
                "title": "T",
                "period": "P",
                "columns": ["", "Amount"],
                "rows": [
                    {"cells": ["Fixed Assets", ""], "style": "group", "indent": 1},
                    {"cells": ["1600 - Vehicles", "$1.00"], "indent": 2},
                    {"cells": ["Total Assets", "$1.00"], "style": "subtotal"},
                ],
            }
        ],
        paper_size="letter",
        landscape=False,
        generated_on="2026-12-31",
    )
    assert 'style="padding-left: 20px">Fixed Assets' in html
    assert 'style="padding-left: 34px">1600 - Vehicles' in html
    assert "<td >Total Assets" in html


def test_the_statement_of_financial_position_groups_too(client, db_session, books):
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    d = client.get(f"/api/reports/statement-of-financial-position?as_of_date={AS_OF}")
    d = d.json()
    assert [g["heading"] for g in d["groups"]["assets"]] == [
        "Current Assets",
        "Fixed Assets",
        "Other Assets",
    ]
    assert d["total_long_term_liabilities"] == 16000
    text = client.get(
        f"/api/reports/statement-of-financial-position/csv?as_of_date={AS_OF}"
    ).text
    assert "  Total Fixed Assets,11000.00" in text


def test_the_accountants_package_prints_the_groups(client, books, monkeypatch):
    """The package builds its statements PDF from _bs_section: the groups
    reach it with no change of its own."""
    from app.services import pdf_service

    captured = {}

    def fake(sections, settings, landscape=False):
        captured["s"] = sections
        return b"%PDF-fake"

    monkeypatch.setattr(pdf_service, "generate_report_pdf", fake)
    r = client.get(
        "/api/reports/accountants-package?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert r.status_code == 200, r.text
    bs = next(s for s in captured["s"] if s["title"] == "Balance Sheet")
    words = [row["cells"][0].strip() for row in bs["rows"]]
    assert "Total Long Term Liabilities" in words and "Fixed Assets" in words
