# ============================================================================
# Tax Report Export — Schedule C (Profit or Loss from Business), and the
# entity returns' page 1: Form 1065, Form 1120, Form 1120-S (#488)
# Feature 19: Generate from P&L data, output PDF and CSV
# ============================================================================

import io
import re
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.models.tax import TaxCategoryMapping
from app.services.accounting import _q

# Schedule C (Form 1040) lines an account can land on, in form order:
# (line id, label, part). "income" lines add to gross income (line 7),
# "deduction" lines (returns, cost of goods sold) subtract from it, and
# "expense" lines are Part II, which total to line 28.
SCHEDULE_C_LINES = [
    ("1", "Gross receipts or sales", "income"),
    ("2", "Returns and allowances", "deduction"),
    ("4", "Cost of goods sold", "deduction"),
    ("6", "Other income", "income"),
    ("8", "Advertising", "expense"),
    ("9", "Car and truck expenses", "expense"),
    ("10", "Commissions and fees", "expense"),
    ("11", "Contract labor", "expense"),
    ("12", "Depletion", "expense"),
    ("13", "Depreciation and section 179 expense", "expense"),
    ("14", "Employee benefit programs", "expense"),
    ("15", "Insurance (other than health)", "expense"),
    ("16a", "Interest - mortgage", "expense"),
    ("16b", "Interest - other", "expense"),
    ("17", "Legal and professional services", "expense"),
    ("18", "Office expense", "expense"),
    ("19", "Pension and profit-sharing plans", "expense"),
    ("20a", "Rent or lease - vehicles, machinery, and equipment", "expense"),
    ("20b", "Rent or lease - other business property", "expense"),
    ("21", "Repairs and maintenance", "expense"),
    ("22", "Supplies", "expense"),
    ("23", "Taxes and licenses", "expense"),
    ("24a", "Travel", "expense"),
    ("24b", "Deductible meals", "expense"),
    ("25", "Utilities", "expense"),
    ("26", "Wages", "expense"),
    ("27a", "Other expenses", "expense"),
    ("27b", "Energy efficient commercial buildings deduction", "expense"),
]
_LINES = {lid: (label, part) for lid, label, part in SCHEDULE_C_LINES}

# The seeded chart (app/seed/chart_of_accounts.py), keyed on the EXACT
# account number. The old table matched number prefixes against a chart the
# app never seeds (6500 Rent landed on Supplies, 6900 Utilities on Rent)
# and compared display strings with `"Line 1" in line`, which also matched
# Lines 10-18 and moved those expenses into gross income. An account not
# listed here falls back by its type (_TYPE_DEFAULT).
DEFAULT_MAPPINGS = {
    "4000": "1",  # Service Income
    "4100": "1",  # Product Sales
    "4200": "1",  # Material Income
    "4300": "1",  # Labor Income
    "4400": "6",  # In-Kind Contributions
    "4900": "6",  # Other Income
    "5000": "4",  # Cost of Goods Sold
    "5100": "4",  # Materials Cost
    "5200": "4",  # Labor Cost
    "5300": "4",  # Subcontractor Costs
    "6000": "8",  # Advertising & Marketing
    "6100": "9",  # Auto & Truck Expense
    "6110": "26",  # Wages & Salaries
    "6120": "23",  # Payroll Tax Expense
    "6130": "15",  # Workers Compensation Insurance
    "6140": "27a",  # Employee Expense Reimbursements
    "6150": "14",  # Employee Benefits Expense
    "6160": "26",  # Paid Time Off Expense (wages)
    "6200": "27a",  # Bank Charges & Fees
    "6300": "15",  # Insurance
    "6400": "18",  # Office Supplies
    "6500": "20b",  # Rent or Lease
    "6600": "21",  # Repairs & Maintenance
    "6700": "25",  # Telephone & Internet
    "6800": "22",  # Tools & Equipment
    "6810": "13",  # Depreciation Expense
    "6900": "25",  # Utilities
    "6950": "27a",  # Miscellaneous Expense
    "6960": "27a",  # Bad Debt Expense
}
_TYPE_DEFAULT = {
    AccountType.INCOME: "1",
    AccountType.COGS: "4",
    AccountType.EXPENSE: "27a",
}
# A bare number people type for a line that the form splits in two.
_ALIASES = {"27": "27a"}
_LINE_ID_RE = re.compile(r"\bline\s*(\d{1,2}[ab]?)\b", re.IGNORECASE)

# The summary rows under the lines, in form order: (data key, label, line).
# The page prints "label (line N)" and the CSV "LABEL (LINE N)".
_SCHEDULE_C_SUMMARY = [
    ("gross_receipts", "Gross receipts", "1"),
    ("returns_and_allowances", "Returns and allowances", "2"),
    ("cost_of_goods_sold", "Cost of goods sold", "4"),
    ("other_income", "Other income", "6"),
    ("gross_income", "Gross Income", "7"),
    ("total_expenses", "Total Expenses", "28"),
    ("net_profit", "Net Profit (Loss)", "31"),
]

# ----------------------------------------------------------------------------
# The entity returns (#488, step 2): Form 1065 for a partnership, Form 1120
# for a C corporation, Form 1120-S for an S corporation, page 1 of each (the
# 2024 revisions). Same mechanism as Schedule C: a user mapping that names a
# line, else the seeded chart's line, else the default for the account's
# type. The seeded expense accounts without a line of their own on the
# corporate forms (insurance, office, telephone, tools, bank fees) land on
# the form's "Other deductions", as a preparer's statement would list them.
# ----------------------------------------------------------------------------

FORM_1065_LINES = [
    ("1a", "Gross receipts or sales", "income"),
    ("1b", "Returns and allowances", "deduction"),
    ("2", "Cost of goods sold", "deduction"),
    (
        "4",
        "Ordinary income (loss) from other partnerships, estates, and trusts",
        "income",
    ),
    ("5", "Net farm profit (loss)", "income"),
    ("6", "Net gain (loss) from Form 4797", "income"),
    ("7", "Other income (loss)", "income"),
    ("9", "Salaries and wages (other than to partners)", "expense"),
    ("10", "Guaranteed payments to partners", "expense"),
    ("11", "Repairs and maintenance", "expense"),
    ("12", "Bad debts", "expense"),
    ("13", "Rent", "expense"),
    ("14", "Taxes and licenses", "expense"),
    ("15", "Interest", "expense"),
    ("16a", "Depreciation", "expense"),
    ("17", "Depletion", "expense"),
    ("18", "Retirement plans, etc.", "expense"),
    ("19", "Employee benefit programs", "expense"),
    ("20", "Energy efficient commercial buildings deduction", "expense"),
    ("21", "Other deductions", "expense"),
]
FORM_1065_DEFAULTS = {
    "4000": "1a",
    "4100": "1a",
    "4200": "1a",
    "4300": "1a",
    "4400": "7",
    "4900": "7",
    "5000": "2",
    "5100": "2",
    "5200": "2",
    "5300": "2",
    "6000": "21",
    "6100": "21",
    "6110": "9",
    "6120": "14",
    "6130": "21",
    "6140": "21",
    "6150": "19",
    "6160": "9",
    "6200": "21",
    "6300": "21",
    "6400": "21",
    "6500": "13",
    "6600": "11",
    "6700": "21",
    "6800": "21",
    "6810": "16a",
    "6900": "21",
    "6950": "21",
    "6960": "12",
}
_FORM_1065_SUMMARY = [
    ("gross_receipts", "Gross receipts", "1a"),
    ("returns_and_allowances", "Returns and allowances", "1b"),
    ("cost_of_goods_sold", "Cost of goods sold", "2"),
    ("gross_profit", "Gross profit", "3"),
    ("other_income", "Other income", "7"),
    ("gross_income", "Total income (loss)", "8"),
    ("total_expenses", "Total deductions", "22"),
    ("net_profit", "Ordinary business income (loss)", "23"),
]

FORM_1120_LINES = [
    ("1a", "Gross receipts or sales", "income"),
    ("1b", "Returns and allowances", "deduction"),
    ("2", "Cost of goods sold", "deduction"),
    ("4", "Dividends and inclusions", "income"),
    ("5", "Interest", "income"),
    ("6", "Gross rents", "income"),
    ("7", "Gross royalties", "income"),
    ("8", "Capital gain net income", "income"),
    ("9", "Net gain or (loss) from Form 4797", "income"),
    ("10", "Other income", "income"),
    ("12", "Compensation of officers", "expense"),
    ("13", "Salaries and wages", "expense"),
    ("14", "Repairs and maintenance", "expense"),
    ("15", "Bad debts", "expense"),
    ("16", "Rents", "expense"),
    ("17", "Taxes and licenses", "expense"),
    ("18", "Interest", "expense"),
    ("19", "Charitable contributions", "expense"),
    ("20", "Depreciation", "expense"),
    ("21", "Depletion", "expense"),
    ("22", "Advertising", "expense"),
    ("23", "Pension, profit-sharing, etc., plans", "expense"),
    ("24", "Employee benefit programs", "expense"),
    ("25", "Energy efficient commercial buildings deduction", "expense"),
    ("26", "Other deductions", "expense"),
]
FORM_1120_DEFAULTS = {
    "4000": "1a",
    "4100": "1a",
    "4200": "1a",
    "4300": "1a",
    "4400": "10",
    "4900": "10",
    "5000": "2",
    "5100": "2",
    "5200": "2",
    "5300": "2",
    "6000": "22",
    "6100": "26",
    "6110": "13",
    "6120": "17",
    "6130": "26",
    "6140": "26",
    "6150": "24",
    "6160": "13",
    "6200": "26",
    "6300": "26",
    "6400": "26",
    "6500": "16",
    "6600": "14",
    "6700": "26",
    "6800": "26",
    "6810": "20",
    "6900": "26",
    "6950": "26",
    "6960": "15",
}
_FORM_1120_SUMMARY = [
    ("gross_receipts", "Gross receipts", "1a"),
    ("returns_and_allowances", "Returns and allowances", "1b"),
    ("cost_of_goods_sold", "Cost of goods sold", "2"),
    ("gross_profit", "Gross profit", "3"),
    ("other_income", "Other income", "10"),
    ("gross_income", "Total income", "11"),
    ("total_expenses", "Total deductions", "27"),
    ("net_profit", "Taxable income before NOL and special deductions", "28"),
]

FORM_1120S_LINES = [
    ("1a", "Gross receipts or sales", "income"),
    ("1b", "Returns and allowances", "deduction"),
    ("2", "Cost of goods sold", "deduction"),
    ("4", "Net gain (loss) from Form 4797", "income"),
    ("5", "Other income (loss)", "income"),
    ("7", "Compensation of officers", "expense"),
    ("8", "Salaries and wages", "expense"),
    ("9", "Repairs and maintenance", "expense"),
    ("10", "Bad debts", "expense"),
    ("11", "Rents", "expense"),
    ("12", "Taxes and licenses", "expense"),
    ("13", "Interest", "expense"),
    ("14", "Depreciation", "expense"),
    ("15", "Depletion", "expense"),
    ("16", "Advertising", "expense"),
    ("17", "Pension, profit-sharing, etc., plans", "expense"),
    ("18", "Employee benefit programs", "expense"),
    ("19", "Energy efficient commercial buildings deduction", "expense"),
    ("20", "Other deductions", "expense"),
]
FORM_1120S_DEFAULTS = {
    "4000": "1a",
    "4100": "1a",
    "4200": "1a",
    "4300": "1a",
    "4400": "5",
    "4900": "5",
    "5000": "2",
    "5100": "2",
    "5200": "2",
    "5300": "2",
    "6000": "16",
    "6100": "20",
    "6110": "8",
    "6120": "12",
    "6130": "20",
    "6140": "20",
    "6150": "18",
    "6160": "8",
    "6200": "20",
    "6300": "20",
    "6400": "20",
    "6500": "11",
    "6600": "9",
    "6700": "20",
    "6800": "20",
    "6810": "14",
    "6900": "20",
    "6950": "20",
    "6960": "10",
}
_FORM_1120S_SUMMARY = [
    ("gross_receipts", "Gross receipts", "1a"),
    ("returns_and_allowances", "Returns and allowances", "1b"),
    ("cost_of_goods_sold", "Cost of goods sold", "2"),
    ("gross_profit", "Gross profit", "3"),
    ("other_income", "Other income", "5"),
    ("gross_income", "Total income (loss)", "6"),
    ("total_expenses", "Total deductions", "21"),
    ("net_profit", "Ordinary business income (loss)", "22"),
]


def _form(
    name, title, lines, defaults, type_default, aliases, summary, receipts, returns
):
    return {
        "name": name,
        "title": title,
        "lines": lines,
        "by_id": {lid: (label, part) for lid, label, part in lines},
        "order": {lid: i for i, (lid, _l, _p) in enumerate(lines)},
        "defaults": defaults,
        "type_default": type_default,
        "aliases": aliases,
        "summary": summary,
        "receipts_line": receipts,
        "returns_line": returns,
    }


# Every return this report can draw, keyed by settings_service.entity_info's
# return_form. Form 990 (a nonprofit) is not among them.
TAX_FORMS = {
    "Schedule C": _form(
        "Schedule C",
        "Schedule C - Profit or Loss from Business",
        SCHEDULE_C_LINES,
        DEFAULT_MAPPINGS,
        _TYPE_DEFAULT,
        _ALIASES,
        _SCHEDULE_C_SUMMARY,
        "1",
        "2",
    ),
    "Form 1065": _form(
        "Form 1065",
        "Form 1065 - U.S. Return of Partnership Income",
        FORM_1065_LINES,
        FORM_1065_DEFAULTS,
        {AccountType.INCOME: "1a", AccountType.COGS: "2", AccountType.EXPENSE: "21"},
        {"1": "1a", "16": "16a"},
        _FORM_1065_SUMMARY,
        "1a",
        "1b",
    ),
    "Form 1120": _form(
        "Form 1120",
        "Form 1120 - U.S. Corporation Income Tax Return",
        FORM_1120_LINES,
        FORM_1120_DEFAULTS,
        {AccountType.INCOME: "1a", AccountType.COGS: "2", AccountType.EXPENSE: "26"},
        {"1": "1a"},
        _FORM_1120_SUMMARY,
        "1a",
        "1b",
    ),
    "Form 1120-S": _form(
        "Form 1120-S",
        "Form 1120-S - U.S. Income Tax Return for an S Corporation",
        FORM_1120S_LINES,
        FORM_1120S_DEFAULTS,
        {AccountType.INCOME: "1a", AccountType.COGS: "2", AccountType.EXPENSE: "20"},
        {"1": "1a"},
        _FORM_1120S_SUMMARY,
        "1a",
        "1b",
    ),
}


def tax_line_id(tax_line: str | None, form: str = "Schedule C") -> str | None:
    """The line of ``form`` a user-configured mapping names ("Line 18",
    "Schedule C, Line 20b", "Line 27 - Other"), or None when it names no
    line that form knows. A mapping is one string per account, so it is
    read against whichever return the company files."""
    if not tax_line:
        return None
    m = _LINE_ID_RE.search(tax_line)
    if not m:
        return None
    spec = TAX_FORMS[form]
    lid = m.group(1).lower()
    lid = spec["aliases"].get(lid, lid)
    return lid if lid in spec["by_id"] else None


def schedule_c_line_id(tax_line: str | None) -> str | None:
    """The Schedule C line a user-configured mapping names (see tax_line_id)."""
    return tax_line_id(tax_line, "Schedule C")


def _label(line_id: str, by_id=None) -> str:
    return f"Line {line_id} - {(by_id or _LINES)[line_id][0]}"


def get_schedule_c_data(db: Session, start_date: date, end_date: date) -> dict:
    """Schedule C data from the P&L accounts (see get_tax_form_data)."""
    return get_tax_form_data(db, "Schedule C", start_date, end_date)


def get_tax_form_data(db: Session, form: str, start_date: date, end_date: date) -> dict:
    """Generate one return's page-1 lines from the P&L accounts.

    Each account lands on one line: the user's mapping when it names a line,
    else the seeded chart's line, else the default for its type. A line's
    amounts are in the line's own sense (receipts and other income as
    credits, returns, COGS and expenses as debits), so net profit always
    equals the Profit & Loss net income, whatever the mapping.

    The result carries the same keys for every form (`gross_receipts`,
    `returns_and_allowances`, `cost_of_goods_sold`, `gross_profit`,
    `other_income`, `gross_income`, `total_expenses`, `net_profit`) and a
    `summary` list naming the form's own words and line for each of the
    ones it prints, so the page and the CSV need no per-form layout.
    """
    spec = TAX_FORMS[form]
    by_id, order = spec["by_id"], spec["order"]
    type_default, defaults = spec["type_default"], spec["defaults"]

    custom_mappings = {
        m.account_id: m.tax_line for m in db.query(TaxCategoryMapping).all()
    }

    # Get all income and expense transactions in period
    results = (
        db.query(
            Account,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Account.account_type.in_(
                [AccountType.INCOME, AccountType.EXPENSE, AccountType.COGS]
            )
        )
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
        .group_by(Account.id)
        .all()
    )

    lines: dict[str, dict] = {}
    for acct, total_debit, total_credit in results:
        custom = custom_mappings.get(acct.id)
        line_id = tax_line_id(custom, form)
        if line_id is None and not custom:
            line_id = defaults.get(acct.account_number or "")
            # The seeded chart's line, only for an account of the seeded
            # kind: an imported chart's 5000 may be Advertising, not COGS.
            type_part = by_id[type_default[acct.account_type]][1]
            if line_id is not None and by_id[line_id][1] != type_part:
                line_id = None
        if line_id is None and not custom:
            line_id = type_default[acct.account_type]

        if line_id is not None:
            key, label, part = line_id, _label(line_id, by_id), by_id[line_id][1]
            sort_key = (order[line_id], "")
        else:
            # A user label that names no line: keep it, and count it where
            # the account's type belongs so the totals still add up.
            fallback = type_default[acct.account_type]
            part = by_id[fallback][1]
            key, label = f"custom:{custom}:{part}", custom
            sort_key = (order[fallback], custom)

        debit = Decimal(str(total_debit or 0))
        credit = Decimal(str(total_credit or 0))
        amount = _q(credit - debit) if part == "income" else _q(debit - credit)

        entry = lines.setdefault(
            key,
            {
                "line": label,
                "line_id": line_id,
                "part": part,
                "accounts": [],
                "total": Decimal("0"),
                "_order": sort_key,
            },
        )
        entry["accounts"].append(
            {
                "account_number": acct.account_number,
                "account_name": acct.name,
                "amount": float(amount),
            }
        )
        entry["total"] += amount

    sorted_lines = sorted(lines.values(), key=lambda ln: ln.pop("_order"))

    def _sum(pred) -> Decimal:
        return sum((ln["total"] for ln in sorted_lines if pred(ln)), Decimal("0"))

    receipts_line, returns_line = spec["receipts_line"], spec["returns_line"]
    gross_receipts = _sum(lambda ln: ln["line_id"] == receipts_line)
    returns = _sum(lambda ln: ln["line_id"] == returns_line)
    cogs = _sum(lambda ln: ln["part"] == "deduction" and ln["line_id"] != returns_line)
    other_income = _sum(
        lambda ln: ln["part"] == "income" and ln["line_id"] != receipts_line
    )
    gross_profit = gross_receipts - returns - cogs  # Schedule C line 5
    gross_income = gross_profit + other_income  # line 7 (total income)
    total_expenses = _sum(lambda ln: ln["part"] == "expense")  # line 28
    net_profit = gross_income - total_expenses  # line 31

    for ln in sorted_lines:
        ln["total"] = float(ln["total"])

    return {
        "form": form,
        "title": spec["title"],
        "summary": [
            {"key": key, "label": label, "line": line}
            for key, label, line in spec["summary"]
        ],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "lines": sorted_lines,
        "gross_receipts": float(gross_receipts),
        "returns_and_allowances": float(returns),
        "cost_of_goods_sold": float(cogs),
        "gross_profit": float(gross_profit),
        "other_income": float(other_income),
        "gross_income": float(gross_income),
        "total_expenses": float(total_expenses),
        "net_profit": float(net_profit),
    }


def export_schedule_c_csv(data: dict) -> str:
    """Export a return's data (get_tax_form_data) as CSV; Schedule C and
    the entity returns print the same way, under the form's own title.

    Uses the shared formula-injection-safe writer because line["accounts"]
    contains user-supplied account names; a chart-of-accounts entry named
    `=HYPERLINK("https://evil.example/")` would otherwise execute on open
    in Excel / Sheets / Numbers.
    """
    from app.services.csv_export import _SafeWriter

    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow([data.get("title") or TAX_FORMS["Schedule C"]["title"]])
    writer.writerow([f"Period: {data['start_date']} to {data['end_date']}"])
    writer.writerow([])
    writer.writerow(["Tax Line", "Account #", "Account Name", "Amount"])

    for line in data["lines"]:
        for acct in line["accounts"]:
            writer.writerow(
                [
                    line["line"],
                    acct["account_number"],
                    acct["account_name"],
                    f"{acct['amount']:.2f}",
                ]
            )
        writer.writerow([f"  Total: {line['line']}", "", "", f"{line['total']:.2f}"])

    writer.writerow([])
    summary = data.get("summary") or [
        {"key": key, "label": label, "line": line}
        for key, label, line in _SCHEDULE_C_SUMMARY
    ]
    for row in summary:
        label = f"{row['label']} (LINE {row['line']})".upper()
        writer.writerow([label, "", "", f"{data[row['key']]:.2f}"])
    writer.writerow([])
    writer.writerow(
        ["DISCLAIMER: This report is for reference only. Consult a tax professional."]
    )

    return output.getvalue()
