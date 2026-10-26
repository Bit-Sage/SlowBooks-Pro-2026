"""The entity returns beside Schedule C (#488, step 2): Form 1065 for a
partnership, Form 1120 for a C corporation, Form 1120-S for an S
corporation, page 1 of each drawn from the P&L the way Schedule C is. The
Schedule C report itself is exactly as before."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import AccountType
from app.models.tax import TaxCategoryMapping
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
from app.services.accounting import create_journal_entry
from app.services.tax_export import (
    DEFAULT_MAPPINGS,
    SCHEDULE_C_LINES,
    TAX_FORMS,
    export_schedule_c_csv,
    get_tax_form_data,
    tax_line_id,
)

PL_TYPES = {"income", "cogs", "expense"}
PART_OF_TYPE = {"income": "income", "cogs": "deduction", "expense": "expense"}
ENTITY_FORMS = ("Form 1065", "Form 1120", "Form 1120-S")


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", list(TAX_FORMS))
def test_every_seeded_pl_account_has_a_line_of_its_own_part(form):
    spec = TAX_FORMS[form]
    for entry in CHART_OF_ACCOUNTS:
        if entry["account_type"] not in PL_TYPES:
            continue
        line = spec["defaults"].get(entry["account_number"])
        assert line is not None, (form, entry["account_number"])
        assert line in spec["by_id"], (form, line)
        assert spec["by_id"][line][1] == PART_OF_TYPE[entry["account_type"]], (
            form,
            entry["account_number"],
        )
    # the type fallbacks and the summary lines are lines the form has
    for line in spec["type_default"].values():
        assert line in spec["by_id"], (form, line)
    assert spec["receipts_line"] in spec["by_id"]
    assert spec["returns_line"] in spec["by_id"]


def test_schedule_c_tables_are_the_ones_from_before():
    spec = TAX_FORMS["Schedule C"]
    assert spec["lines"] is SCHEDULE_C_LINES
    assert spec["defaults"] is DEFAULT_MAPPINGS
    assert [row[2] for row in spec["summary"]] == ["1", "2", "4", "6", "7", "28", "31"]


def test_the_corporate_forms_put_wages_and_bad_debts_on_their_own_lines():
    assert TAX_FORMS["Form 1120-S"]["defaults"]["6110"] == "8"  # Salaries and wages
    assert TAX_FORMS["Form 1120-S"]["defaults"]["6960"] == "10"  # Bad debts
    assert TAX_FORMS["Form 1120-S"]["defaults"]["6810"] == "14"  # Depreciation
    assert TAX_FORMS["Form 1120-S"]["defaults"]["6000"] == "16"  # Advertising
    assert TAX_FORMS["Form 1120"]["defaults"]["6110"] == "13"
    assert TAX_FORMS["Form 1120"]["defaults"]["6960"] == "15"
    assert TAX_FORMS["Form 1120"]["defaults"]["6000"] == "22"
    assert TAX_FORMS["Form 1065"]["defaults"]["6110"] == "9"
    assert TAX_FORMS["Form 1065"]["defaults"]["6960"] == "12"
    assert TAX_FORMS["Form 1065"]["defaults"]["6810"] == "16a"
    # no advertising line on Form 1065: other deductions
    assert TAX_FORMS["Form 1065"]["defaults"]["6000"] == "21"


def test_tax_line_id_reads_a_mapping_against_the_form():
    assert tax_line_id("Line 7", "Form 1120-S") == "7"
    assert tax_line_id("Form 1120-S, Line 1", "Form 1120-S") == "1a"  # alias
    assert tax_line_id("Line 16", "Form 1065") == "16a"
    assert tax_line_id("Line 27", "Form 1120") is None  # the total line is not mappable
    assert tax_line_id("Line 27", "Schedule C") == "27a"
    assert tax_line_id("", "Form 1120") is None
    assert tax_line_id("Officers", "Form 1120") is None


# ---------------------------------------------------------------------------
# The reports
# ---------------------------------------------------------------------------

EXPENSES = [
    a["account_number"] for a in CHART_OF_ACCOUNTS if a["account_type"] == "expense"
]


def _post(db, accts, debit_number, credit_number, amount):
    create_journal_entry(
        db,
        date(2026, 5, 1),
        "x",
        [
            {"account_id": accts[debit_number].id, "debit": amount, "credit": 0},
            {"account_id": accts[credit_number].id, "debit": 0, "credit": amount},
        ],
    )


@pytest.fixture
def year_of_books(db_session, seed_accounts):
    _post(db_session, seed_accounts, "1000", "4000", Decimal("1000.00"))
    _post(db_session, seed_accounts, "1000", "4100", Decimal("500.00"))
    _post(db_session, seed_accounts, "4100", "1000", Decimal("50.00"))  # a return
    _post(db_session, seed_accounts, "5100", "1000", Decimal("200.00"))  # materials
    _post(db_session, seed_accounts, "1000", "4900", Decimal("25.00"))  # other income
    spent = {}
    for i, number in enumerate(EXPENSES):
        amount = Decimal("10.00") + Decimal(i)
        _post(db_session, seed_accounts, number, "1000", amount)
        spent[number] = amount
    db_session.commit()
    return spent


def _report(client):
    r = client.get("/api/tax/return?start_date=2026-01-01&end_date=2026-12-31")
    assert r.status_code == 200, r.text
    return r.json()


def _line_of(data, number):
    for line in data["lines"]:
        for acct in line["accounts"]:
            if acct["account_number"] == number:
                return line
    raise AssertionError(f"{number} is on no line")


def _net_income(client):
    return client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31"
    ).json()["net_income"]


def test_an_s_corp_gets_form_1120_s(client, year_of_books):
    client.put("/api/settings", json={"entity_type": "s_corp"})
    data = _report(client)
    assert data["form"] == "Form 1120-S"
    assert data["title"].startswith("Form 1120-S - ")
    assert _line_of(data, "6110")["line"] == "Line 8 - Salaries and wages"
    assert _line_of(data, "6960")["line"] == "Line 10 - Bad debts"
    assert _line_of(data, "6400")["line"] == "Line 20 - Other deductions"
    assert _line_of(data, "4000")["line"] == "Line 1a - Gross receipts or sales"
    assert _line_of(data, "5100")["line"] == "Line 2 - Cost of goods sold"
    assert _line_of(data, "4900")["line"] == "Line 5 - Other income (loss)"
    assert Decimal(str(data["total_expenses"])) == sum(year_of_books.values())
    assert data["gross_receipts"] == 1450.0  # 1,500 less the 50 return on 4100
    assert data["cost_of_goods_sold"] == 200.0
    assert data["other_income"] == 25.0
    assert data["gross_profit"] == 1250.0
    assert data["gross_income"] == 1275.0
    assert Decimal(str(data["net_profit"])) == Decimal(str(_net_income(client)))
    assert [row["line"] for row in data["summary"]] == [
        "1a",
        "1b",
        "2",
        "3",
        "5",
        "6",
        "21",
        "22",
    ]
    assert data["summary"][-1]["label"] == "Ordinary business income (loss)"


def test_a_partnership_gets_form_1065_and_a_c_corp_form_1120(client, year_of_books):
    client.put("/api/settings", json={"entity_type": "llp"})
    data = _report(client)
    assert data["form"] == "Form 1065"
    assert _line_of(data, "6110")["line"].startswith("Line 9 - Salaries and wages")
    assert _line_of(data, "6810")["line"] == "Line 16a - Depreciation"
    assert data["summary"][-1] == {
        "key": "net_profit",
        "label": "Ordinary business income (loss)",
        "line": "23",
    }
    assert Decimal(str(data["net_profit"])) == Decimal(str(_net_income(client)))

    client.put(
        "/api/settings", json={"entity_type": "llc", "llc_tax_election": "c_corp"}
    )
    data = _report(client)
    assert data["form"] == "Form 1120"
    assert _line_of(data, "6110")["line"] == "Line 13 - Salaries and wages"
    assert _line_of(data, "6000")["line"] == "Line 22 - Advertising"
    assert data["summary"][-1]["line"] == "28"
    assert Decimal(str(data["net_profit"])) == Decimal(str(_net_income(client)))


def test_a_sole_proprietor_gets_schedule_c_from_the_same_route(client, year_of_books):
    client.put("/api/settings", json={"entity_type": "sole_proprietor"})
    data = _report(client)
    assert data["form"] == "Schedule C"
    schedule_c = client.get(
        "/api/tax/schedule-c?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert data == schedule_c


def test_the_return_route_refuses_an_unset_entity_and_a_nonprofit(
    client, seed_accounts
):
    r = client.get("/api/tax/return")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_entity_return"
    assert "entity type" in r.json()["detail"]["message"]
    client.put(
        "/api/settings", json={"company_type": "nonprofit", "entity_type": "s_corp"}
    )
    for path in ("/api/tax/return", "/api/tax/return/csv"):
        r = client.get(path)
        assert r.status_code == 409, path
        assert "Form 990" in r.json()["detail"]["message"]
    # Schedule C's own gate is untouched
    assert client.get("/api/tax/schedule-c").status_code == 409


def test_a_user_mapping_names_the_forms_own_line(
    client, db_session, year_of_books, seed_accounts
):
    client.put("/api/settings", json={"entity_type": "s_corp"})
    db_session.add(
        TaxCategoryMapping(account_id=seed_accounts["6110"].id, tax_line="Line 7")
    )
    db_session.add(
        TaxCategoryMapping(account_id=seed_accounts["6400"].id, tax_line="Software")
    )
    db_session.commit()
    data = _report(client)
    assert _line_of(data, "6110")["line"] == "Line 7 - Compensation of officers"
    # a label that names no line is kept, counted among the deductions
    assert _line_of(data, "6400")["line"] == "Software"
    assert _line_of(data, "6400")["part"] == "expense"
    assert Decimal(str(data["total_expenses"])) == sum(year_of_books.values())


def test_the_csv_prints_the_forms_title_and_summary(client, year_of_books):
    client.put("/api/settings", json={"entity_type": "s_corp"})
    r = client.get("/api/tax/return/csv?start_date=2026-01-01&end_date=2026-12-31")
    assert r.status_code == 200
    assert "form_1120_s_2026-01-01_2026-12-31.csv" in r.headers["content-disposition"]
    text = r.text.lstrip("﻿")
    assert text.startswith("Form 1120-S - U.S. Income Tax Return for an S Corporation")
    assert "ORDINARY BUSINESS INCOME (LOSS) (LINE 22)" in text
    assert "TOTAL DEDUCTIONS (LINE 21)" in text
    assert "GROSS RECEIPTS (LINE 1A)" in text
    assert "Line 8 - Salaries and wages" in text


def test_the_schedule_c_csv_is_as_before(client, year_of_books):
    r = client.get("/api/tax/schedule-c/csv?start_date=2026-01-01&end_date=2026-12-31")
    text = r.text.lstrip("﻿")
    assert text.startswith("Schedule C - Profit or Loss from Business")
    for label in (
        "GROSS RECEIPTS (LINE 1)",
        "RETURNS AND ALLOWANCES (LINE 2)",
        "COST OF GOODS SOLD (LINE 4)",
        "OTHER INCOME (LINE 6)",
        "GROSS INCOME (LINE 7)",
        "TOTAL EXPENSES (LINE 28)",
        "NET PROFIT (LOSS) (LINE 31)",
    ):
        assert label in text, label


def test_export_csv_without_summary_falls_back_to_schedule_c(db_session, year_of_books):
    data = get_tax_form_data(
        db_session, "Schedule C", date(2026, 1, 1), date(2026, 12, 31)
    )
    data.pop("summary")
    data.pop("title")
    text = export_schedule_c_csv(data)
    assert text.startswith("Schedule C - Profit or Loss from Business")
    assert "NET PROFIT (LOSS) (LINE 31)" in text


def test_the_type_fallback_is_the_forms_other_deductions(db_session, seed_accounts):
    # an account the seed does not know lands by its type on each form
    from app.models.accounts import Account

    acct = Account(
        account_number="6999", name="Odd Expense", account_type=AccountType.EXPENSE
    )
    db_session.add(acct)
    db_session.flush()
    _post(db_session, seed_accounts | {"6999": acct}, "6999", "1000", Decimal("7.00"))
    db_session.commit()
    for form, line in (
        ("Form 1065", "Line 21 - Other deductions"),
        ("Form 1120", "Line 26 - Other deductions"),
        ("Form 1120-S", "Line 20 - Other deductions"),
        ("Schedule C", "Line 27a - Other expenses"),
    ):
        data = get_tax_form_data(db_session, form, date(2026, 1, 1), date(2026, 12, 31))
        assert _line_of(data, "6999")["line"] == line, form
