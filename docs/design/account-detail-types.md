# Account detail types — design (#390)

Status: DESIGN 2026-10-22. Written against the 2.26.0 tree. Nothing here is
committed product behavior. Issue #390, from Kim Morden Bookkeeping's notes:
a CPA reading the Balance Sheet expects current and long-term liabilities
grouped apart, and the chart cannot say which is which.

## What exists today

**Six types.** `AccountType` in `app/models/accounts.py` is `asset`,
`liability`, `equity`, `income`, `expense`, `cogs`, stored on
`accounts.account_type`. Beside it, `accounts.bank_kind` is `"bank"`,
`"credit_card"` or NULL; the register, the feed queue, reconciliation,
transfers and every paid-from / deposit-to picker key off it (#114), and
`_check_bank_kind` in `app/routes/accounts.py` holds the only type rule
there is: a bank is an asset, a card is a liability. There is no `role`
column; the posting code finds its structural accounts by number through
`app/services/control_accounts.py` (1100 Accounts Receivable, 2000 Accounts
Payable, 1200 Undeposited Funds, 1300 Inventory, 2200 Sales Tax Payable…),
and the docstring there already names a role column as the real fix.

**Sub-accounts.** `parent_id` is a free pointer. The route refuses only
"an account cannot be its own parent"; nothing checks that a child's type
matches its parent's, and the account form in `app/static/js/app.js`
(`showAccountForm`) has no parent field and no bank-kind field at all —
number, name, type, description. Parents and bank kinds arrive from the
seed, the importers and the API.

**The seed** (`app/seed/chart_of_accounts.py`, QuickBooks 2003's
Contractor template) already carries the accounts the detail types are
for, flat: 1400 Prepaid Expenses, 1500 Equipment, 1510 Accumulated
Depreciation, 1600 Vehicles, 1700 Other Assets, 2400 Loan Payable, 2500
Other Current Liabilities, 4900 Other Income — every one typed plain
`asset`, `liability` or `income`.

**Statements group by type only.**

- `balance_sheet` in `app/routes/reports/financial.py` calls
  `_totals_by_account` once per type and returns three flat lists,
  `assets`, `liabilities`, `equity`, with `total_assets`,
  `total_liabilities`, `total_equity`. Net income to date is synthesized as
  an equity line named `Net Income (current period)` (account_id None).
  Prior-year compare (#347; the 2.26.0 polish is #367) is `_bs_with_prior`
  → `_with_prior`, which pairs lines across the two years by
  `account_id` and copies the keys in `_BS_TOTALS` into `prior`.
- The screen (`app/static/js/reports.js` `balanceSheet`), the PDF
  (`_bs_section`) and the CSV (`app/services/ledger_exports.py`
  `balance_sheet_csv`) each walk the same three keys with the headings
  Assets / Liabilities / Equity, the totals Total Assets / Total
  Liabilities / Total Equity, and the grand total `Liabilities + Equity`.
  The accountant's package (`app/routes/reports/package.py`) reuses
  `_bs_section` and `_pl_section` for its statements PDF; there is no XLSX
  writer anywhere in the app (the only openpyxl import is the migration
  reader).
- `profit_loss` returns `income`, `cogs`, `expenses` and the totals in
  `_PL_TOTALS` (`total_income`, `total_cogs`, `gross_profit`,
  `total_expenses`, `net_income`). The P&L by Class / by Job grids
  (`_pl_pivot`) bucket by `section_of = {INCOME, COGS, EXPENSE}`.
- The nonprofit statements (`app/services/nonprofit_reports.py`) and the
  dashboard's `balance_sheet_trend` do the same three-type arithmetic.
- The cash flow statement (`app/services/cash_flow.py` `_place`) is the
  one report that already *needs* the split, and guesses it: a four-digit
  asset number under 1500 is working capital, 1500 and up is investing; a
  liability is financing if its name matches `_LONG_TERM_DEBT`
  (`loan|notes payable|mortgage|long-term|line of credit|lease liability|
  bonds payable`), otherwise working capital.

**The QuickBooks lists import** (`app/services/iif_common.py`
`IIF_TO_ACCOUNT_TYPE`) collapses fifteen ACCNTTYPEs to six: BANK / AR /
OCASSET / OASSET / FIXASSET → asset; AP / CCARD / OCLIAB / LTLIAB →
liability; EQUITY; INC and EXINC → income; EXP and EXEXP → expense; COGS;
NONPOSTING → asset. `iif_import.py` sets `bank_kind` from BANK / CCARD.
The export direction (`account_to_iif_type`) has nothing to read back, so
it guesses from the number: 1000–1099 BANK, 1100 AR, <1500 OCASSET, <2000
FIXASSET, else OASSET; 2000 AP, <2500 OCLIAB, else LTLIAB. On the seed
chart that sends 2400 Loan Payable out as OCLIAB and 2500 Other Current
Liabilities out as LTLIAB, and never emits CCARD. The other importers
(`chart_import.py` `TYPE_WORDS`, `xero_import.py`, `wave_import.py`,
`sage_import.py`, `zoho_import.py`, `myob_import.py`, `gnucash_import.py`)
each accept the finer words — "fixed asset", "long term liability",
"non-current liability", "other long-term liability", Sage's "14" — and
throw them away the same way. `qbo_export_import.py` recognizes "Current
Assets", "Fixed Assets", "Other Current Liabilities", "Long-Term
Liabilities" as headings in a QuickBooks Online report and keeps only the
parent section (and maps "Cost of Goods Sold" to `expense`, not `cogs`).

**Where the type name shows.** The chart page groups by type with the
headings in `typeNames` and prints the raw enum in the Type column; the
form's Type select capitalizes the enum; the chart CSV export
(`csv_export.export_accounts`) writes it under "Type". `Terms`
(`app/services/terminology.py`) swaps statement words — Income → Revenue &
Support, Equity → Net Assets — never the enum values. Every account picker
filters with `pickerAccounts(accounts, types)` in `utils.js` on the base
type.

## The proposal

One new column, `accounts.detail_type`, a string enum with QuickBooks'
fifteen names. `account_type` stays: the detail type refines it and may
never contradict it.

| `detail_type` | Label | Base `account_type` |
|---|---|---|
| `bank` | Bank | asset |
| `accounts_receivable` | Accounts Receivable | asset |
| `other_current_asset` | Other Current Asset | asset |
| `fixed_asset` | Fixed Asset | asset |
| `other_asset` | Other Asset | asset |
| `accounts_payable` | Accounts Payable | liability |
| `credit_card` | Credit Card | liability |
| `other_current_liability` | Other Current Liability | liability |
| `long_term_liability` | Long Term Liability | liability |
| `equity` | Equity | equity |
| `income` | Income | income |
| `cost_of_goods_sold` | Cost of Goods Sold | cogs |
| `expense` | Expense | expense |
| `other_income` | Other Income | income |
| `other_expense` | Other Expense | expense |

**Loan is not a detail type.** QuickBooks Desktop has none; a loan is an
Other Current Liability or a Long Term Liability by its term, which is
exactly the distinction Kim wants on the statement. (QuickBooks Online's
"Loan Payable" and "Notes Payable" are sub-detail types under those two.)
The seeded 2400 Loan Payable defaults to Long Term Liability; see the
migration.

**The pair is enforced**, in one place, `BASE_TYPE_OF` in
`app/models/accounts.py` beside the enum, and checked by a
`_check_detail_type` next to `_check_bank_kind` in `routes/accounts.py`:

- `detail_type` given → its base must equal `account_type`, else 400
  `detail_type 'long_term_liability' is a liability detail type; the
  account's type is 'asset'`.
- `bank_kind == "bank"` ⇔ `detail_type == "bank"`, `"credit_card"` ⇔
  `"credit_card"`. `bank_kind` stays a column — eight JS modules and the
  register key off it — and is kept consistent rather than derived, for
  now. Deriving it is a later cleanup, not this change.
- Control accounts keep their refusal on `account_type`; `detail_type` on
  1100 and 2000 is locked to AR / AP the same way (the chart page already
  shows the lock note).
- A sub-account shares its parent's detail type. Checked on create and
  whenever `parent_id` or `detail_type` changes; existing rows are made
  consistent by the migration (below) rather than refused later. Same
  detail type, not merely same base type: a Fixed Asset parent with an
  Other Current Asset child would print under two headings, which is the
  thing a sub-account exists to avoid.

**Defaults per type**, used by the migration and by any API client that
sends only `account_type`:

| `account_type` | default `detail_type` |
|---|---|
| asset | `other_current_asset` (`bank` when `bank_kind == "bank"`) |
| liability | `other_current_liability` (`credit_card` when `bank_kind == "credit_card"`) |
| equity | `equity` |
| income | `income` |
| cogs | `cost_of_goods_sold` |
| expense | `expense` |

### Migration

One Alembic revision on the single chain after `d3e4f5a6b7c8`
(`users_session_version`, 2026-10-21), run per company file like every
other. Adds the column nullable, backfills, then sets NOT NULL (SQLite via
`batch_alter_table`, as the existing revisions do). Backfill order:

1. `bank_kind` → `bank` / `credit_card`.
2. Control numbers: 1100 → `accounts_receivable`, 2000 →
   `accounts_payable`.
3. Seeded accounts the type alone gets wrong, matched on number **and**
   the seeded name (the `nonprofit_only` matcher's rule, so a renumbered or
   renamed account is left to the default): 1500 Equipment, 1510
   Accumulated Depreciation, 1600 Vehicles → `fixed_asset`; 1700 Other
   Assets → `other_asset`; 2400 Loan Payable → `long_term_liability`;
   4900 Other Income → `other_income`.
4. Everything else → the type's default.
5. Children take their parent's detail type, walking the chain top down,
   when the base types agree; a child whose base type differs from its
   parent's (possible today, nothing stopped it) keeps its own default and
   is listed in the migration log.

**The risk**: an imported or hand-made fixed asset, a mortgage, an
owner's truck loan, a long-term note — any of these typed plain `asset` or
`liability` — lands in Other Current Asset / Other Current Liability.
The default never changes a figure, only the heading a line sits under,
and the statements keep their current layout until the second PR, so the
window to fix it is the whole first release. The user corrects it on the
chart page, where the field is editable like the type is today, and the
page groups by detail type under each type heading, so a Loan Payable
sitting under "Other Current Liabilities" is seen in the first scroll.
The CHANGELOG entry gets one line: "after upgrading, open Chart of
Accounts and check the detail type of any loan, vehicle, equipment or
long-term note". No one-time modal; a notice that cannot know whether it
is right is noise, and the chart page is the review.

## Balance Sheet

The response keeps `assets`, `liabilities`, `equity` as flat lists — that
is what `_with_prior` pairs across years and what the drill-down links
(`account_id` → `/account-transactions`) read — and every line gains
`detail_type`. New totals, added to `_BS_TOTALS` so the prior-year copy
carries them:

```
assets
  Current Assets            bank, accounts_receivable, other_current_asset
    Total Current Assets                         total_current_assets
  Fixed Assets              fixed_asset
    Total Fixed Assets                           total_fixed_assets
  Other Assets              other_asset
    Total Other Assets                           total_other_assets
  Total Assets                                   total_assets
liabilities
  Current Liabilities       accounts_payable, credit_card, other_current_liability
    Total Current Liabilities                    total_current_liabilities
  Long Term Liabilities     long_term_liability
    Total Long Term Liabilities                  total_long_term_liabilities
  Total Liabilities                              total_liabilities
equity                      equity, then Net Income (current period)
  Total Equity                                   total_equity
Liabilities + Equity
```

A group with no accounts is left out (a service company with no fixed
assets does not get an empty "Fixed Assets" heading and a zero subtotal);
Total Assets and Total Liabilities always print. Within a group, lines
keep `_account_order` (number or name, the `sort` param). Order of
detail types inside Current Assets is Bank, AR, Other Current Asset, as
QuickBooks prints them; inside Current Liabilities, AP, Credit Card,
Other Current Liability.

One helper, `_bs_groups(lines) -> [(heading, subtotal_key, lines)]`, is
written once in `financial.py` and used by the three writers: the screen
table in `reports.js` (the API hands the groups down ready-made as
`groups` beside the flat lists, so the JS classifies nothing),
`_bs_section` for the PDF (the package reuses it unchanged) and
`balance_sheet_csv`, whose "Section" column becomes the group heading
with the base section kept in a new "Type" column. Prior-year compare:
the subtotal rows pass through `_compare_figures` like the totals do; the
$ / % change columns need nothing new. `Equity` and `Liabilities +
Equity` keep going through `T()`.

The nonprofit Statement of Financial Position
(`nonprofit_reports.statement_of_financial_position`) builds the same
asset and liability lists and takes the same grouping on those two sides;
the Net Assets side is untouched. `balance_sheet_trend` stays on base
types — it charts three lines.

`cash_flow._place` reads the detail type instead of the number range and
the name regex: `fixed_asset` and `other_asset` → investing,
`long_term_liability` → financing, every other current item → working
capital. The `_ACCUMULATED` name match stays for the one case the detail
type cannot settle: accumulated depreciation is a Fixed Asset in
QuickBooks too, and an adjustment on the cash flow.

## Profit & Loss

```
Income                       income            Total Income
Cost of Goods Sold           cost_of_goods_sold  Total Cost of Goods Sold
Gross Profit
Expenses                     expense           Total Expenses
Net Operating Income
Other Income                 other_income      Total Other Income
Other Expense                other_expense     Total Other Expense
Net Other Income
Net Income
```

`profit_loss` gains `other_income` and `other_expense` lists and the
totals `net_operating_income`, `total_other_income`, `total_other_expense`,
`net_other_income`, all appended to `_PL_TOTALS`; `income` and `expenses`
no longer contain the other-* accounts. `net_income` is the same number as
today (the move is between sections, never in or out of the total), so
the Balance Sheet's net income line and `books_balance` invariants are
unchanged. The Other Income / Other Expense block prints only when either
list has a line, so a company with nothing in 4900 sees the P&L it sees
now plus one "Net Operating Income" row — and that row too can be left out
when both lists are empty, to keep today's layout byte-for-byte for most
companies; decide in review. The screen, `_pl_section` and
`profit_loss_csv` take the same `_pl_groups` treatment as the Balance
Sheet. "Other Income" through `T()` becomes "Other Revenue & Support" by
itself, since the word-swap matches "Income" inside it; check that reads
well for a nonprofit, else add the key.

**Grids and nonprofit variants stay on base type** in the first cut:
`_pl_pivot` (P&L by Class, by Job, Unclassified), the Statement of
Activities and the functional-expense report group by `AccountType` and
nothing in them is per detail type for free. The consequence is that the
grid's "Total Income" includes other income while the P&L's does not;
`Net Income` ties either way. Open question 3.

## Chart of accounts page

**One combined picker**, replacing the six-entry Type select: an
`<optgroup>` per base type, the fifteen labels inside, default "Expense".
`account_type` is set from the choice in `saveAccount` (the API also
accepts both and checks the pair). The reasons for one picker over two:
QuickBooks Desktop's New Account dialog is one list, so the vocabulary
Kim's clients know is the detail type and the base type is the heading
over it; and two selects need JS to keep them agreeing. The locked
control-account note covers the detail type too. The page keeps its six
type headings and adds a second-level heading per detail type under each;
the Type column shows the label ("Long Term Liability"). A parent picker
is out of scope here (the form never had one), but the sub-account rule
is enforced on the API path so an importer or client cannot create a
mismatch.

**Lists import.** `IIF_TO_ACCOUNT_TYPE` becomes `IIF_TO_DETAIL_TYPE`, the
base type derived:

| ACCNTTYPE | `detail_type` |
|---|---|
| BANK | bank |
| AR | accounts_receivable |
| OCASSET | other_current_asset |
| FIXASSET | fixed_asset |
| OASSET | other_asset |
| AP | accounts_payable |
| CCARD | credit_card |
| OCLIAB | other_current_liability |
| LTLIAB | long_term_liability |
| EQUITY | equity |
| INC | income |
| COGS | cost_of_goods_sold |
| EXP | expense |
| EXINC | other_income |
| EXEXP | other_expense |
| NONPOSTING | other_current_asset (as today's asset) |

`_account_at_path(db, segments, acct_type)` keeps matching on base type,
so a re-import still finds the chart's existing accounts; an import never
changes an existing account's detail type (the user's edit wins), and the
dry run lists any that differ from the file's. The other importers' maps
become `(detail_type)` tables with the base derived — each already has
the words, it only has to stop throwing them away: `TYPE_WORDS` ("fixed
asset", "long term liability", "other income", …), Xero ("non-current
asset" → other_asset, "non-current liability" → long_term_liability),
Wave ("property, plant, equipment" → fixed_asset, "loan and line of
credit" and "other long-term liability" → long_term_liability), Sage
(5 / 6 → fixed_asset, 8 → other_asset, 14 → long_term_liability, 1 / 10 →
AR / AP), Zoho and MYOB one-to-one. GnuCash has no finer types; defaults.
The QBO report importer keeps the innermost `_SECTIONS` heading it passed
through and maps it ("fixed assets", "long-term liabilities", "other
income", …) — and "Cost of Goods Sold" should become `cogs` while there;
`expense` there looks like a slip, not a choice.

**IIF export.** `account_to_iif_type` reads the detail type back through
the inverse table and drops the number-range guess entirely; CCARD is
emitted for the first time. `tests/test_interchange_roundtrip.py` then
tests a real round trip instead of "the export emits a type the import
knows".

**Chart CSV export** adds a "Detail type" column after "Type"; the import
reads it when present and falls back to the type words when not.

## API

- `AccountCreate`: `detail_type: Optional[DetailType] = None`. Omitted →
  the default for `account_type` and `bank_kind`. Given → the pair check.
  So every client that sends only `account_type` today keeps working and
  gets the migration's defaults.
- `AccountUpdate`: `detail_type` optional. Changing `account_type` without
  `detail_type` resets the detail type to the new type's default (the old
  one would be a contradiction); changing both is checked as a pair;
  changing `bank_kind` is checked against the resulting detail type.
  `parent_id` changes check the sub-account rule.
- `AccountResponse`: `detail_type` and `detail_type_label` (the table's
  label, so the JS and the AI tools do not keep a copy of the list).
- `GET /api/accounts?detail_type=long_term_liability` filter beside the
  `account_type` one.
- Errors are 400 with the sentence, in the house style ("… is a liability
  detail type; the account's type is 'asset'", "a sub-account of 1500
  Equipment (Fixed Asset) must be a Fixed Asset too").
- `ai_tools.list_accounts` / `get_account` carry the field; nothing in
  the AI actions creates accounts.

## Tests to write

- `tests/test_account_detail_types.py`: the pair check on create and
  update, both directions; defaults when omitted; `bank_kind` ⇔ `bank` /
  `credit_card`; the reset on a type change; the sub-account rule on
  create, `parent_id` change and `detail_type` change; control accounts
  locked to AR / AP; the list filter.
- Migration (the `test_entity_classes_survive_migration.py` pattern): a
  2.26 chart with the seed, an imported mortgage, a renamed 1500, a
  parent/child pair, a child of the other base type — every backfill row
  above, the NOT NULL, SQLite and PostgreSQL.
- `test_interchange_roundtrip.py`: fifteen ACCNTTYPEs round trip exactly;
  2400 exports as LTLIAB; a card as CCARD.
- `test_chart_import.py`, `test_migration_sources.py`: one chart per
  importer with the finer words, asserting the detail types.
- Balance Sheet: groups and subtotals, empty groups omitted, order inside
  a group follows `sort`, Total Assets and `Liabilities + Equity` equal
  to the 2.26 figures for the same ledger, prior-year compare carries the
  subtotals and pairs a long-term account present in one year only,
  drill-down ids intact, CSV columns, `_bs_section` rows, the package PDF.
- P&L: the other block below Net Operating Income, `net_income` equal to
  the 2.26 figure, the block omitted when empty, compare, CSV, PDF, class
  filter.
- SoFP groups assets and liabilities; Statement of Activities unchanged.
- Cash flow: a `long_term_liability` named "Trent's truck" is financing; a
  `fixed_asset` numbered 1450 is investing (both wrong today).
- `test_books_balance_invariants.py` passes unchanged; the browser test
  (`test_browser_report_views.py` pattern) covers the picker and headings.

## Rollout: two PRs

**PR 1 — the field.** Model, migration, schemas, route checks, the chart
page picker and grouping, the importers' maps, the IIF export's reverse
map, the CSV export column, tests. No statement changes a number or a
heading. Every company file migrates on first open; users have the whole
release to correct the few accounts the defaults get wrong, and nothing
they see yet depends on it.

**PR 2 — the statements.** Balance Sheet, P&L, their CSV / PDF writers
and the package, SoFP, cash flow. Reviewed by Kim against a real chart
before it goes out, because the only judgment call in it — which seeded
and imported accounts land where — has already been made by then and is
visible on the chart page.

Shipping both at once would put a regrouped Balance Sheet in front of a
CPA on the same day the defaults are guessed. Two PRs keeps the guess and
the consequence a release apart.

## Open questions for Trent

1. **Loan default.** The migration sends the seeded 2400 Loan Payable to
   Long Term Liability. Kim's clients' loans are mostly multi-year, but a
   line of credit in the same account is current. Agree LTL as the default,
   with the chart page as the correction?
2. **More than one AR / AP account.** QuickBooks allows several Accounts
   Receivable accounts; the posting code here only ever uses 1100 / 2000.
   Allow the `accounts_receivable` / `accounts_payable` detail types on
   other accounts (they print under Current Assets / Current Liabilities
   either way) or restrict them to the control numbers?
3. **The grids.** Leave P&L by Class / by Job on base type (their "Total
   Income" then includes other income while the P&L's does not), or add
   Other Income / Other Expense rows to `_pl_pivot` in PR 2 so the three
   reports tie line for line? The second is a day's work and the tests
   already compare the grid to the P&L.
4. **Net Operating Income row.** Always print it, or only when there is
   something in Other Income / Other Expense? Always is what QuickBooks
   does; only-when-needed keeps most companies' P&L exactly as it is.
5. **`bank_kind`.** Keep it as a column kept consistent with the detail
   type (this note), or derive it in PR 1 and drop the column in a later
   release? Keeping it means one more pair to validate; deriving it means
   touching ten JS modules now.
6. **Accumulated depreciation.** Typed Fixed Asset, as QuickBooks does, so
   it nets inside the Fixed Assets group — or its own contra line? The
   cash flow report still needs to tell it apart (by name today).
