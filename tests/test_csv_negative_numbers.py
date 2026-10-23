"""SlowBooks-Pro-Testing#101: the CSV formula guard took a loss (-1234.56)
for a formula and wrote it as text ('-1234.56), so it would not sum. A cell
that is only a signed number is written as is; anything else starting with
= + - @ TAB CR still gets the apostrophe."""

import csv
import io

import pytest

from app.services.csv_export import _csv_safe, _Identifier, _SafeWriter


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.parametrize(
    "number",
    ["-1234.56", "-0.5", "-0", "+1234.56", "-$1,234.56", "-1,234,567.00"],
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
        # a leading + with no decimal point is a phone or a code, not money;
        # a leading zero is a reference, not the number it would round to
        "+3",
        "+15551234567",
        "+447911123456",
        "-0001",
        "+0001",
    ],
)
def test_a_formula_or_identifier_is_still_prefixed(formula):
    assert _csv_safe(formula) == "'" + formula


def test_an_identifier_column_keeps_a_plain_number_as_text():
    # a cell the exporter marks as an identifier is never treated as an
    # amount, even when it would otherwise be a plain number
    assert _csv_safe(_Identifier("-1234.56")) == "'-1234.56"
    assert _csv_safe(_Identifier("+15551234567")) == "'+15551234567"
    # but a value with no formula lead is left alone (nothing to neutralise)
    assert _csv_safe(_Identifier("5551234567")) == "5551234567"


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


def test_a_phone_column_keeps_a_plus_prefixed_number_as_text(db_session):
    # a phone like +15551234567 was written bare once numbers were left
    # alone, so a spreadsheet dropped the + (or showed 4.47911E+11); the
    # Phone column is marked an identifier so it stays verbatim (#410)
    from app.models.contacts import Customer, Vendor
    from app.services.csv_export import export_customers, export_vendors

    db_session.add(Customer(name="Acme", is_active=True, phone="+15551234567"))
    db_session.add(Vendor(name="Supplier", is_active=True, phone="+447911123456"))
    db_session.commit()

    cust_rows = _rows(export_customers(db_session))
    assert ["'+15551234567"] == [r[4] for r in cust_rows if r and r[0] != "ID"]

    vend_rows = _rows(export_vendors(db_session))
    assert ["'+447911123456"] == [r[4] for r in vend_rows if r and r[0] != "ID"]


def test_an_iif_memo_that_is_signed_digits_stays_text(db_session):
    # nothing numeric goes through _iif_text (amounts are written directly),
    # so a memo like "-0001" keeps its apostrophe for anyone editing the IIF
    # file in Excel (#410)
    from app.services.iif_export import _iif_text

    assert _iif_text("-0001") == "'-0001"
    assert _iif_text("+15551234567") == "'+15551234567"
    assert _iif_text("=HYPERLINK(1)") == "'=HYPERLINK(1)"


def test_the_audit_log_before_after_keeps_an_identifier_as_text():
    # a before/after snapshot is read, not summed: a phone or a reference
    # that changed must stay verbatim, not open as a number (#410)
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.routes.reports.package import audit_csv

    row = SimpleNamespace(
        action="UPDATE",
        table_name="customers",
        record_id=1,
        changed_fields=["phone"],
        old_values={"phone": "+15551234567"},
        new_values={"phone": "-0001"},
        timestamp=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        username="alice",
    )
    rows = _rows(audit_csv([row], "Acme", "2026-01-01", "2026-12-31"))
    change = [r for r in rows if len(r) > 4 and r[4] == "phone"]
    assert change and change[0][5] == "'+15551234567"
    assert change[0][6] == "'-0001"


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
