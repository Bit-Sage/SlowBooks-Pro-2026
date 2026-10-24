# ============================================================================
# Default Chart of Accounts — the entries QuickBooks 2003 Pro created when
# you ran the "EasyStep Interview" for a new company file. Account numbers
# match its "Contractor" industry template.
#
# "detail_type" (#390) is given where the type's default is wrong for the
# account; every other account takes the default (Bank for a bank_kind,
# AR / AP on 1100 / 2000, else Other Current Asset, Expense, ...).
# ============================================================================

CHART_OF_ACCOUNTS = [
    # Assets (1000s)
    {
        "account_number": "1000",
        "name": "Checking",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {
        "account_number": "1010",
        "name": "Savings",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {"account_number": "1100", "name": "Accounts Receivable", "account_type": "asset"},
    {"account_number": "1200", "name": "Undeposited Funds", "account_type": "asset"},
    {"account_number": "1300", "name": "Inventory", "account_type": "asset"},
    {"account_number": "1400", "name": "Prepaid Expenses", "account_type": "asset"},
    {
        "account_number": "1500",
        "name": "Equipment",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1510",
        "name": "Accumulated Depreciation",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1600",
        "name": "Vehicles",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1700",
        "name": "Other Assets",
        "account_type": "asset",
        "detail_type": "other_asset",
    },
    # Liabilities (2000s)
    {"account_number": "2000", "name": "Accounts Payable", "account_type": "liability"},
    {
        "account_number": "2100",
        "name": "Credit Card",
        "account_type": "liability",
        "bank_kind": "credit_card",
    },
    {
        "account_number": "2200",
        "name": "Sales Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2300",
        "name": "Payroll Liabilities",
        "account_type": "liability",
    },
    {
        "account_number": "2310",
        "name": "Federal Income Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2320",
        "name": "State Income Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2330",
        "name": "Social Security Payable",
        "account_type": "liability",
    },
    {"account_number": "2340", "name": "Medicare Payable", "account_type": "liability"},
    {"account_number": "2350", "name": "FUTA Payable", "account_type": "liability"},
    {
        "account_number": "2360",
        "name": "State Unemployment (SUTA) Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2370",
        "name": "Other Payroll Deductions Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2380",
        "name": "Employee Benefits Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2390",
        "name": "Accrued PTO Liability",
        "account_type": "liability",
    },
    {
        "account_number": "2400",
        "name": "Loan Payable",
        "account_type": "liability",
        "detail_type": "long_term_liability",
    },
    {
        "account_number": "2500",
        "name": "Other Current Liabilities",
        "account_type": "liability",
    },
    # Equity (3000s)
    {"account_number": "3000", "name": "Owner's Equity", "account_type": "equity"},
    {"account_number": "3100", "name": "Owner's Draw", "account_type": "equity"},
    {"account_number": "3200", "name": "Retained Earnings", "account_type": "equity"},
    # Income (4000s)
    {"account_number": "4000", "name": "Service Income", "account_type": "income"},
    {"account_number": "4100", "name": "Product Sales", "account_type": "income"},
    {"account_number": "4200", "name": "Material Income", "account_type": "income"},
    {"account_number": "4300", "name": "Labor Income", "account_type": "income"},
    # 4400 In-Kind Contributions, like the net-asset accounts, is a nonprofit
    # account: it is created when the company switches to nonprofit, or by
    # the first in-kind gift (accounting.ensure_nonprofit_accounts), never
    # seeded into a business chart.
    {
        "account_number": "4900",
        "name": "Other Income",
        "account_type": "income",
        "detail_type": "other_income",
    },
    # COGS (5000s)
    {"account_number": "5000", "name": "Cost of Goods Sold", "account_type": "cogs"},
    {"account_number": "5100", "name": "Materials Cost", "account_type": "cogs"},
    {"account_number": "5200", "name": "Labor Cost", "account_type": "cogs"},
    {"account_number": "5300", "name": "Subcontractor Costs", "account_type": "cogs"},
    # Expenses (6000s)
    {
        "account_number": "6000",
        "name": "Advertising & Marketing",
        "account_type": "expense",
    },
    {
        "account_number": "6100",
        "name": "Auto & Truck Expense",
        "account_type": "expense",
    },
    {"account_number": "6110", "name": "Wages & Salaries", "account_type": "expense"},
    {
        "account_number": "6120",
        "name": "Payroll Tax Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6130",
        "name": "Workers Compensation Insurance",
        "account_type": "expense",
    },
    {
        "account_number": "6140",
        "name": "Employee Expense Reimbursements",
        "account_type": "expense",
    },
    {
        "account_number": "6150",
        "name": "Employee Benefits Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6160",
        "name": "Paid Time Off Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6200",
        "name": "Bank Charges & Fees",
        "account_type": "expense",
    },
    {"account_number": "6300", "name": "Insurance", "account_type": "expense"},
    {"account_number": "6400", "name": "Office Supplies", "account_type": "expense"},
    {"account_number": "6500", "name": "Rent or Lease", "account_type": "expense"},
    {
        "account_number": "6600",
        "name": "Repairs & Maintenance",
        "account_type": "expense",
    },
    {
        "account_number": "6700",
        "name": "Telephone & Internet",
        "account_type": "expense",
    },
    {"account_number": "6800", "name": "Tools & Equipment", "account_type": "expense"},
    {
        "account_number": "6810",
        "name": "Depreciation Expense",
        "account_type": "expense",
    },
    {"account_number": "6900", "name": "Utilities", "account_type": "expense"},
    {
        "account_number": "6950",
        "name": "Miscellaneous Expense",
        "account_type": "expense",
    },
    {"account_number": "6960", "name": "Bad Debt Expense", "account_type": "expense"},
]
