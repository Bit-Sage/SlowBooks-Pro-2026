"""SlowBooks-Pro-Testing#101: the CSV formula guard took a loss (-1234.56)
for a formula and wrote it as text ('-1234.56), so it would not sum. A cell
that is only a signed number is written as is; anything else starting with
= + - @ TAB CR still gets the apostrophe."""

import csv
import io

import pytest

from app.services.csv_export import _csv_safe, _SafeWriter


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.parametrize(
    "number", ["-1234.56", "-0.5", "-0", "+3", "-$1,234.56", "-1,234,567.00"]
)
def test_a_plain_signed_number_is_not_prefixed(number):
    assert _csv_safe(number) == number


@pytest.mark.parametrize(
    "formula",
    [
        "=1+1",
        "-1+1",
        "-2*3",
        "-SUM(A1)",
        "+SUM(A1)",
        "@x",
        "-cmd|' /C calc'!A0",
        "-1e5",
        "-1,23",
        "-1234.",
        "-",
        "\t-1",
        "\r5",
        "-1234.56\n=1",
    ],
)
def test_a_formula_is_still_prefixed(formula):
    assert _csv_safe(formula) == "'" + formula


def test_the_writer_keeps_numbers_and_guards_text():
    out = io.StringIO()
    _SafeWriter(out).writerow(["=HYPERLINK(1)", "-1234.56", "-SUM(A1)", -5])
    assert _rows(out.getvalue()) == [["'=HYPERLINK(1)", "-1234.56", "'-SUM(A1)", "-5"]]


def test_a_schedule_c_loss_is_a_plain_number():
    from app.services.tax_export import export_schedule_c_csv

    data = {
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "lines": [
            {
                "line": "Line 1",
                "total": -1234.56,
                "accounts": [
                    {
                        "account_number": "4000",
                        "account_name": "-SUM(A1)",
                        "amount": -1234.56,
                    }
                ],
            }
        ],
        "gross_receipts": -1234.56,
        "returns_and_allowances": 0,
        "cost_of_goods_sold": 0,
        "other_income": 0,
        "gross_income": -1234.56,
        "total_expenses": 0,
        "net_profit": -1234.56,
    }
    rows = _rows(export_schedule_c_csv(data))
    assert ["Line 1", "4000", "'-SUM(A1)", "-1234.56"] in rows
    net = [r for r in rows if r and r[0] == "NET PROFIT (LOSS) (LINE 31)"]
    assert net == [["NET PROFIT (LOSS) (LINE 31)", "", "", "-1234.56"]]
    assert "'-1234.56" not in export_schedule_c_csv(data)


def test_a_ledger_and_a_reconciliation_csv_negative_stays_a_number():
    from app.routes.reports.package import sections_csv
    from app.services.ledger_exports import trial_balance_csv

    tb = trial_balance_csv(
        {
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "items": [
                {
                    "account_number": "6000",
                    "account_name": "@Rent",
                    "account_type": "expense",
                    "total_debit": 0,
                    "total_credit": 1234.56,
                    "net_balance": -1234.56,
                }
            ],
            "total_debit": 0,
            "total_credit": 1234.56,
            "difference": -1234.56,
        },
        "Acme",
    )
    assert ["6000", "'@Rent", "expense", "0.00", "1234.56", "-1234.56"] in _rows(tb)

    rec = sections_csv(
        [
            {
                "title": "Reconciliation",
                "period": "x",
                "columns": ["Line", "Amount"],
                "rows": [{"cells": ["Difference", "-$1,234.56"]}],
            }
        ],
        "Acme",
    )
    assert ["Difference", "-$1,234.56"] in _rows(rec)
