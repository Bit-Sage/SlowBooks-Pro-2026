# Data Model

Schema reference for the Slowbooks PostgreSQL database. 55 tables on
a double-entry accounting foundation. For migration history, see the
files under `migrations/versions/`; for model code, see `app/models/`.

| Table | Purpose |
|-------|---------|
| `accounts` | Chart of Accounts — asset, liability, equity, income, expense, COGS |
| `customers` | Customer contacts with billing/shipping addresses |
| `vendors` | Vendor contacts |
| `items` | Product/service/material/labor items with rates |
| `invoices` | Invoice headers with status tracking |
| `invoice_lines` | Invoice line items |
| `estimates` | Estimate headers |
| `estimate_lines` | Estimate line items |
| `payments` | Payment records |
| `payment_allocations` | Maps payments to invoices (many-to-many) |
| `transactions` | Journal entry headers |
| `transaction_lines` | Journal entry splits (debit OR credit); `cleared` / `reconciliation_id` on bank-account lines |
| `accounts.bank_kind` | `bank` / `credit_card` — which chart accounts the register, pickers and transfers use |
| `bank_accounts` | A bank feed: the statement identity of a ledger account (`account_id`); `legacy_balance` = the pre-2.10 register balance until posted or dismissed |
| `bank_transactions` | Statement lines (feeds, OFX/CSV imports) with `match_status` and the ledger line they match (`transaction_line_id`) |
| `reconciliations` | Reconciliation sessions over a ledger account's lines (`account_id`, `beginning_balance`, `cleared_total`) |
| `settings` | Company settings key-value store |
| `audit_log` | Automatic change tracking for all entities |
| `purchase_orders` | Purchase order headers |
| `purchase_order_lines` | PO line items with received quantities |
| `bills` | Vendor bills (AP mirror of invoices) |
| `bill_lines` | Bill line items with expense account tracking |
| `bill_payments` | Bill payment records |
| `bill_payment_allocations` | Maps bill payments to bills |
| `credit_memos` | Customer credit memos |
| `credit_memo_lines` | Credit memo line items |
| `vendor_credits` | Supplier credits against Accounts Payable |
| `vendor_credit_lines` | Vendor credit line items (carry the expense account, like bill lines) |
| `vendor_credit_applications` | Which bill a vendor credit settled, and for how much |
| `credit_applications` | Maps credit memos to invoices |
| `recurring_invoices` | Recurring invoice templates |
| `recurring_invoice_lines` | Recurring invoice line items |
| `email_log` | Email delivery history |
| `tax_category_mappings` | Account-to-tax-line mappings; one string per account, read against the return the entity type files (Schedule C, Form 1065, 1120 or 1120-S) (#488) |
| `backups` | Backup file records |
| `companies` | Multi-company database list |
| `employees` | Employee records for payroll |
| `pay_runs` | Pay run headers with totals |
| `pay_stubs` | Individual pay stubs with withholding breakdowns |
| `qbo_mappings` | Which local record a QuickBooks Online record became, written by the connector retired in 2.23.2; read so those documents still void and edit right |
| `attachments` | File attachments linked to invoices, bills, etc. |
| `bank_rules` | Pattern-matching rules for auto-categorizing bank imports |
| `budgets` | Budget amounts by account and period |
| `email_templates` | Customizable email templates |
| `inventory_movements` | Per-item qty/cost ledger (purchases, sales, adjustments) |
| `saved_reports` | Named (report_type + parameters) tuples |
| `document_audits` | SHA-256 hash chain for generated tax-form PDFs (W-2/W-3/940/941) |
| `portal_accesses` | Audit log for self-service portal hits (success + failure) |
| `login_attempts` | Authentication-attempt audit log |
| `reseller_permits` | Per-entity sales-tax reseller permits with expiration + verification trail |

## jobs (Customer:Job / Projects)

| column | notes |
|---|---|
| id, customer_id (FK customers) | a job belongs to exactly one customer |
| name, job_number | name unique per customer, case-insensitive (API-enforced) |
| status | pending · awarded · in_progress · closed · not_awarded |
| job_type, description, site_address, notes | free text |
| start_date, projected_end_date, end_date | |
| contract_amount | Numeric(15,2), drives billed-vs-contract |
| is_active | inactive = hidden from pickers, kept on history |

`job_id` (nullable FK) lives on: transactions, transaction_lines, invoices,
invoice_lines, bills, bill_lines, estimates, estimate_lines, purchase_orders,
purchase_order_lines, credit_memos, vendor_credits, vendor_credit_lines,
recurring_invoices, time_entries.
`class_id` was added to transaction_lines, invoice_lines and bill_lines.
Attribution rule for reports: `coalesce(line.job_id, transaction.job_id)`.
Migration `e9f0a1b2c3d4_add_jobs`.

## cost_codes (job-costing chart)

| column | notes |
|---|---|
| id, code (unique, case-insensitive via API; normalized to the `DD SS SS` division/section shape) | e.g. `03` Concrete, `03 30 00` Cast-in-Place Concrete |
| level | `division` · `section` · null (a custom, non-standard code) |
| cost_type | per-code default: labor · material · subcontract · equipment · other |
| account_id (FK accounts, nullable) | default posting account |
| parent_id (self FK) | a section's division; set automatically |
| notes, is_active | inactive = hidden from pickers |

`cost_code_id` (nullable FK) on transaction_lines, bill_lines, invoice_lines,
estimate_lines, purchase_order_lines, vendor_credit_lines, time_entries. A
nullable `cost_type` (String(20)) rides every costable line —
transaction_lines, bill_lines, estimate_lines, purchase_order_lines,
vendor_credit_lines — chosen per line, falling back to the code's default when
blank. `is_billable` + `billed_invoice_line_id` (FK invoice_lines) on
transaction_lines and bill_lines. `purchase_orders.job_id` /
`purchase_order_lines.job_id` feed committed cost. Migrations
`f0a1b2c3d4e5_add_cost_codes`, `c8e4a2f1b3d5_division_section_cost_codes`
(#550: `level`, the four line `cost_type` columns, trade packages).

## Trade packages (#550)

| table | purpose |
|---|---|
| trade_packages | id, name (unique), notes, is_active, sort_order, timestamps — the bid packages a GC awards |
| trade_package_sections | (package_id, cost_code_id) composite PK — a package ↔ cost-code section, many-to-many |

A package is what a sub bids; a section is what costs post to. Seeded by
`POST /api/construction/setup` when the company has none.

## Construction estimating (#551)

`estimate_lines` gains the bid shape: `trade_package_id` (nullable FK
trade_packages, SET NULL — the bid package the line belongs to; null groups
under "Unassigned"), `line_kind` (String(12), NOT NULL default `base` — one of
`base` · `allowance` · `alternate` · `unit_price`), `unit` (String(20),
nullable — SF, LF, EA, CY…) and `alternate_accepted` (Boolean, NOT NULL
default false). `estimates` gains the four compounding markups `gc_pct`,
`insurance_pct`, `fee_pct`, `bond_pct` (Numeric(7,4), NOT NULL default 0 —
percent values, 5.5 = 5.5%), `markup_allowances` (Boolean, NOT NULL
default false — the markups also build on allowance lines when true) and `accepted_at` (DateTime, nullable).
`job_budgets.locked` (Boolean, NOT NULL default false) marks the rows an
accepted estimate owns — only a change order moves them. `invoice_lines` gains
`cost_type` (String(20)) and `unit_cost` (Numeric(12,4)), both nullable, so a
converted estimate carries its cost basis onto the invoice. The bid math
(lines at cost; the markup base, the four markups compounding in order, and
their total; the subtotal of the included lines; the pre-tax bid total as
subtotal + markups, with tax covering the lines and markups together;
priced-direct and allowance totals; the package, alternate and unit-price
breakdowns) is computed, not stored — `app/services/estimating.py`. Migration
`d9f6b2c4e7a1_construction_estimating` (additive and nullable/defaulted;
downgrade reverses).

## The contract on the job (#552)

`jobs` gains the contract terms, all nullable (a job with no `contract_type`
behaves exactly as before): `contract_type` (String(12) — `lump_sum` · `gmp` ·
`cost_plus`), `original_contract_amount` (Numeric(15,2) — the sum when the
contract was first set, stamped once; `contract_amount` stays the current /
revised sum so #554 shows original + changes = revised), `fee_pct`
(Numeric(7,4)) and `fee_amount` (Numeric(15,2) — a fixed fee, which wins when
both are set), `cap_amount` (Numeric(15,2) — the GMP maximum or a cost-plus
not-to-exceed), `contingency_amount` (Numeric(15,2) — owner contingency inside
the sum), `retainage_pct` (Numeric(5,2) — held per pay application, used by
#555), `contract_date` (Date) and `contract_estimate_id` (FK estimates, SET
NULL).

New table `sov_lines` — the owner's schedule of values:

| column | notes |
|---|---|
| id, job_id (FK jobs, CASCADE) | the job this schedule belongs to |
| line_no (Integer), item_no (String(10)) | 1-based order; printed number ("01"…), editable |
| description (String(200)) | |
| trade_package_id (FK, SET NULL), cost_code_id (FK, SET NULL) | optional links |
| kind (String(16), NOT NULL default `work`) | `work` · `allowance` · `contingency` · `fee` · `general_conditions` |
| scheduled_value (Numeric(15,2), NOT NULL default 0) | |
| source_estimate_id (FK estimates, SET NULL) | set when built from an estimate |

Unique `(job_id, line_no)`. A `scheduled_value` may be negative (a deduct
alternate nets the sum down). No billed / stored / retainage columns yet —
#555 adds them when progress billing bills the SOV. The contract sum and the
schedule are **pre-tax** (tax is added per invoice). The contract summary
(changes, fee, cap remaining, contingency remaining, SOV total and balance,
billed to date) is computed, not stored — `app/services/contracts.py`.
Migration `e2a4c6d8f0b1_construction_contract` (additive and
nullable/defaulted; downgrade reverses).

## Job cost model (milestone 3)

| table | purpose |
|---|---|
| cost_types | code (key), name, is_labor, burden_pct, default_account_id, offset_account_id, burden_offset_account_id, sort_order, is_active — seeded labor/material/subcontract/equipment/other |
| cost_codes.parent_id | hierarchy (self FK) |
| equipment | code, name, hourly_rate, cost_code_id, recovery_account_id |
| job_costs | number (JC-000001), date, job_id (NULL for allocations), memo, source manual/time_entry/allocation, status posted/void, transaction_id, total |
| job_cost_lines | job_id, cost_code_id, cost_type, qty/rate/amount, debit_account_id, credit_account_id, employee_id, equipment_id, time_entry_id, is_burden, is_billable |
| job_budgets | job_id × (cost_code_id and/or cost_type, or neither), amount, revenue_amount, source manual/estimate/change, estimate_id — the unique key is (job, code, type), so a row may carry both |
| employees.cost_rate, burden_pct · estimate_lines.unit_cost · time_entries.job_cost_id · transaction_lines.cost_type | |

Migration `a1b2c3d4e5f6_job_cost_model`.

