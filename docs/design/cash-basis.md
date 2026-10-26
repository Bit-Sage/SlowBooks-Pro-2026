# Reports on a cash basis — design (#483)

Status: DESIGN + step 1 built on this branch (#483, 2026-10-26). The
architecture — a basis on every ledger line, written when the document
posts, never recomputed when a report is read — was decided by Keith on
2026-10-26. Step 1 (below) is built here; "Decisions" at the end records
what the build settled that this text had not, and "Steps 2+" what is
left. Issue #483, from Trent: every statement is accrual only, nothing in
`app/` knows a basis, and the CPAs who file most small-business returns
file them on the cash basis, so the bookkeeper switches the P&L at year
end. QuickBooks has a company default and a per-report toggle.

## What exists today

**One ledger, one reading.** `Transaction` / `TransactionLine` in
`app/models/transactions.py` are the whole double-entry engine: every
document posts a balanced entry through `create_journal_entry` in
`app/services/accounting.py` (sum of debits equals sum of credits, cents
exact, the closing date checked once for every path), and every report
sums the lines. Nothing on a line says *when* its figure counts.

**A/R and A/P are the only accruals.** An invoice posts Dr 1100 Accounts
Receivable / Cr income accounts (one per line, the item's or 4000) / Cr
2200 Sales Tax Payable (`_build_invoice_journal_lines`,
`app/routes/invoices/helpers.py`). A customer payment posts Dr bank or
1200 Undeposited Funds / Cr 1100, with a realized-FX line when the
currencies differ (`create_payment`, `app/routes/payments.py`); the
unallocated part of a payment stays a credit on 1100 until
`POST /api/payments/{id}/apply` moves it onto invoices, which posts
nothing but FX. A batch payment (`app/routes/batch_payments.py`) and an
online payment (`app/services/payments/recorder.py`) post the same two
lines. A sales receipt is an invoice and a payment on the same day
(`app/routes/sales_receipts.py`). A bill posts Dr each line's expense,
cost of goods or inventory account (tax spread over the lines) / Cr 2000
Accounts Payable (`_post_bill_lines`, `app/routes/bills.py`); a bill
payment posts Dr 2000 / Cr bank (`app/routes/bill_payments.py`). A credit
memo posts Dr income / Cr 1100 and applying it to an invoice posts
nothing; a small-balance write-off is a credit memo with `is_write_off`
whose income side is 6960 Bad Debt Expense (`app/routes/credit_memos.py`).
A vendor credit is the mirror. Everything else — expenses, checks, card
charges, register entries, deposits straight to income, transfers,
payroll runs, journal entries, opening balances, depreciation — is cash
or neither and reads the same under either basis.

**Voids reverse, edits repost.** A void posts the mirror image of the
original lines (`reversing_lines`, or the per-route copy of it) on the
original date, under the closing date, and refuses a line that cleared in
a completed reconciliation (`bank_posting.assert_not_reconciled`). A paid
invoice or bill cannot be voided until its payments are
(`void_invoice`, `void_bill`). An edit of a posted invoice or bill
deletes the entry's lines and reposts them under the same transaction
id (`_reverse_and_delete_journal`, `existing_transaction`), allowed
while the new total is not below what is already paid.

**The stored balance** `Account.balance` is a running total kept beside
the lines (`apply_balance_deltas`), recomputed from them at every start
(`rebuild_stored_balances`); the Chart of Accounts, the accounts export
and the AI tools read it.

**The reports.** `_totals_by_account` in `app/routes/reports/financial.py`
is the one sum behind the P&L (`profit_loss` → `_pl_period`) and the
Balance Sheet (`balance_sheet` → `_bs_period`, which also derives the
"Net Income (current period)" equity line from the same sums). Both take
`sort`, `compare=prior_year`, and the P&L `exclude_adjusting` (#341),
`class_id` and `columns`; the PDF and CSV routes call the same functions
and print the same figures. The Trial Balance, General Ledger, P&L Detail
(`app/services/pl_detail.py`), the grids (`_pl_pivot`), the nonprofit
statements, the dashboard, the registers, the agings and about a hundred
other query sites sum `TransactionLine` directly.

## The proposal

**A basis on every line.** `transaction_lines.basis`, a `String(10)`,
one of:

| `basis` | Meaning | Written by |
|---|---|---|
| `both` | the figure is the same under either basis | the default: every cash expense, deposit, transfer, payroll run, journal entry, opening balance… and the bank and A/R or A/P sides of a payment |
| `accrual` | counts only on the accrual basis | every line of an invoice, bill, credit memo, vendor credit, late fee and their voids and edit adjustments (`ACCRUAL_SOURCE_TYPES` in `app/services/cash_basis.py`) |
| `cash` | counts only on the cash basis | the recognition lines a customer payment or bill payment writes (below) |

**Two readings of one ledger.** The accrual view is `both + accrual`; the
cash view is `both + cash`. Each view must balance on its own, so the
rule `create_journal_entry` enforces is: the whole entry balances (as
today), *and* its `accrual` lines balance among themselves, *and* its
`cash` lines balance among themselves. An entry that breaks it is refused
as an unbalanced entry is.

That rule is why the payment's A/R line is `both`, not `accrual`: a
payment is Dr bank 100 (both) / Cr A/R 100 (both), and its cash
recognition is a self-balancing pair, Dr A/R 100 (cash) / Cr income 100
(cash). In the accrual view A/R is relieved and income was recognized by
the invoice; in the cash view the invoice is invisible, A/R nets to zero
on the payment, and income lands on the payment's date. Bank is `both`,
so bank balances are identical under either basis.

**The cash recognition lines** are written into the payment's own journal
entry, dated the payment:

- For each allocation to an invoice, the invoice's recognition lines —
  every line of its entry except A/R: income per item, the discount
  debit, sales tax payable, a QuickBooks Online balancing line, plus any
  late fee posted on it — prorated by `applied / invoice total` in home
  currency, each share to the cent with the rounding cent on the largest
  share, carrying the line's class, job, cost code, cost type and function
  (there is no item column on a ledger line; the item's income account is
  what the line carries). Same side as the original (income stays a
  credit, a discount stays a debit). One balancing Dr A/R (cash) for the
  applied amount.
- For a bill payment, the same from the bill's lines (expense, cost of
  goods, inventory asset, with the tax already inside each) as debits and
  a Cr A/P (cash) for the applied amount.
- For the unallocated part of a payment (a prepayment, an overpayment, a
  payment taken without choosing invoices): Dr A/R (cash) / Cr 4950
  Unapplied Cash Payment Income (cash), QuickBooks' rule. A bill payment's
  remainder: Dr 6990 Unapplied Cash Bill Payment Expense (cash) / Cr A/P
  (cash). Both accounts are created on demand (`ensure_account`, as 3900
  and 6999 are).
- When that remainder is later applied (`POST /api/payments/{id}/apply`),
  cash lines appended to the payment's own entry move it: Dr Unapplied
  Cash Payment Income / Cr the invoice's recognition lines, prorated as
  above. So they sit on the payment's date: the cash was in hand that
  day, the period's cash-basis net income does not change (unapplied
  cash income was income already), only the account it sits in, and
  applying still posts no new transaction. Voiding the payment reverses
  the entry whole.

**Reading a basis.** `ledger_basis` is a session execution option (one
`do_orm_execute` listener on `Session`, beside the model in
`app/models/transactions.py`): every ORM select that touches `TransactionLine` — a column
sum, a join, a subquery, a relationship load — carries
`basis != 'cash'` unless the query asks for `ledger_basis="cash"`
(`basis != 'accrual'`) or `"all"`. So every reader that exists today,
and every one written without thinking about it, keeps the accrual
figures it had: the General Ledger, the registers, the agings, the
dashboard, the IIF export, `rebuild_stored_balances`. Step 1's two
reports opt in; the paths that must see every line (a void copying the
entry it reverses) ask for `"all"` through `all_lines`. The stored
balance never takes a cash line (`apply_balance_deltas` is handed none).

**The switch.** A company default, `report_basis` in `DEFAULT_SETTINGS`
(`accrual` | `cash`, default `accrual`, Settings → Company), and a
`basis` query parameter on the report (`?basis=cash`, `accrual`, or
omitted for the default) — QuickBooks' shape. The response says which
(`"basis"`), the PDF and CSV take the same parameter and print "Cash
basis" in the period line (the accrual line stays as it is, so a file
saved yesterday still matches), and the screen offers it beside Sort by.

### Migration

One Alembic revision after `c8e4a2f1b3d5` (cost codes by division,
2026-10-26): `transaction_lines.basis` NOT NULL, server default `'both'`,
so every existing row and every row an old build inserts is `both`
(SQLite adds the column in one `ALTER TABLE`; PostgreSQL the same). No
index: `basis != 'cash'` is not selective.

**The backfill** is `cash_basis.backfill(db)`, run at every start beside
`rebuild_stored_balances` (not inside the migration — a migration must
not import the models, which describe a later schema; the detail-type
migration keeps its own copies for the same reason, and the start-up
pass is what repairs a file a build between the two wrote). It is
idempotent and cheap when there is nothing to do:

1. Every line of an entry whose `source_type` is an accrual one and
   whose basis is still `both` → `accrual`. (A reversal entry is matched
   by its own source type, `invoice_void`, `bill_void`…)
2. Every customer payment and bill payment that is not void, has an
   entry, and has no `cash` line yet, in date order: its allocations are
   prorated and written as above, on the payment's date; the remainder
   as unapplied. An allocation made later through `/apply` cannot be
   told from one made on the day, so all of them land on the payment's
   date. A payment's entry gets its cash lines exactly once.
3. Nothing is written for a voided payment (its entry and its reversal
   both carry no cash lines, and net to zero in either view).

After it, the invariant below holds for the file; the test suite proves
it on a seeded one. Importers (IIF, QuickBooks Online, the ledger
imports) that post a payment entry without cash lines are caught by the
same pass at the next start, so a payment entry is never left with a
reading only one basis can give.

### The invariant

For any period, in the cash view: cash-basis net income equals the change
in cash (bank, credit card and Undeposited Funds balances) plus the
change in the other balance-sheet accounts that a cash-basis book still
carries, which is what the cash Balance Sheet says:

```
NI_cash(period) = Δ cash
                + Δ other assets         (fixed assets bought, inventory paid for, prepaid, retainage receivable…)
                − Δ liabilities           (loans, cards, sales tax collected not remitted, payroll liabilities, customer deposits)
                − Δ equity ex. net income (owner contributions and draws, opening balance equity)
```

with A/R and A/P at zero. The tests state it as: the cash Balance Sheet
balances; 1100 and 2000 read zero on it; and `net_income` on the cash
P&L equals the change in the cash accounts plus the named non-cash
movements of the scenario, exactly (no sales tax: net income is the
change in cash to the cent).

## The hard cases, and step 1's rule for each

**Partial payments.** Prorated by amount over the document's recognition
lines, every payment independently (`spread`'s rule: whole cents, the
leftover cent on the share that lost most). Over three payments an
income line can end a cent off its accrual figure while A/R nets to zero
exactly; the final payment does not true it up (there is no column tying
a cash line to its document, and the cent belongs to no period more than
another). Accepted for v1; a `recognizes_invoice_id` column would let a
later step true up.

**Credits applied, discounts, write-offs.** A credit memo is an accrual
entry (Dr income / Cr A/R); applying it to an invoice posts nothing, and
the invoice's later cash is still prorated over the invoice's *total*,
so the credited part of the income is never recognized on the cash basis
— which is right: no cash came for it. A discount line is a debit on the
item's account and is prorated with the rest. A small-balance write-off
is a credit memo whose income side is Bad Debt Expense, so on the cash
basis neither the written-off income nor the bad debt ever appears.

**Prepayments and unapplied cash.** QuickBooks' rule, above: Unapplied
Cash Payment Income on the payment's date, moved to the invoice's income
when applied — on the payment's date too (decision 6). The vendor side
mirrors it with Unapplied Cash Bill Payment Expense; a vendor prepayment
has no apply-later route yet, so it stays there until step 2.

**Voiding or editing a paid document.** The cash lines belong to the
payment's entry, so voiding the payment reverses them (the void reads
every line, `all_lines`, and `reversing_lines` carries each line's
basis), and the invoice or bill can then be voided as an accrual entry.
A paid or partly paid invoice or bill whose lines are edited keeps the
cash lines its earlier payments wrote: cash was recognized on the lines
as they stood when it arrived, A/R and A/P still net to zero in the cash
view, and the accrual entry is reposted as today. To move recognized
income to another account, void the payment, edit, receive it again.
(The step-2 cleanup: recompute a payment's cash lines when its document
is edited, unless the payment's period is closed.)

**Sales tax payable.** Prorated with the invoice's lines, so on the cash
basis 2200 carries the tax *collected* and not yet remitted, and the
sales-tax payment (Dr 2200 / Cr bank, `both`) takes it down. The Sales
Tax Liability report stays accrual in step 1.

**Payroll.** A pay run posts wages and employer taxes as expense against
net pay (bank) and the withholdings and employer taxes (payroll
liabilities), all `both` in step 1. Under a strict cash basis the
liability half is an expense when the tax deposit is made, not when the
run posts; QuickBooks treats payroll liabilities the same way it treats
other current liabilities on a cash-basis report (they stay). Step 3
tags a pay run's liability-side expense lines `accrual` and writes cash
lines on the liability payment; the invariant above already names
payroll liabilities among the non-cash movements.

**Journal entries that hit A/R or A/P directly.** `both`, as every
journal entry: a hand-posted A/R line counts on both bases and is not
relieved by a payment's cash pair (a payment cannot be applied to a
journal entry). QuickBooks' rule — such a line follows the document it
is "linked" to — needs a customer on the line, which the journal form
does not have. Opening A/R and A/P balances entered as journal entries
sit on the cash Balance Sheet until step 2 gives the journal form a
basis choice.

**Retainage and progress billing.** #552 (retainage) and #555 (pay
applications) are not on `main`. When they land, an invoice's A/R debit
splits between 1100 and 1150 Retainage Receivable and a payment can
release held retainage long after the work was billed; the recognition
pair must then prorate across the retainage held and the retainage
released, and a bill's over 2050 Retainage Payable. Left for the
follow-up, with the rule already in place: a release is a payment, and a
payment writes the cash lines for what it collected.

**Inventory and cost of goods.** The stock movement an invoice posts (Dr
cost of goods / Cr inventory, under the invoice's own source) is among
the invoice's recognition lines, so on the cash basis the cost of a sale
lands with its income, in the paid proportion; a bill's inventory debit
likewise lands when the bill is paid. The cash Balance Sheet carries the
inventory paid for.

**Multi-currency.** A payment relieves A/R at each invoice's booked rate
(`ar_home_credits`), and the cash pair is built from the home-currency
lines of the invoice's entry at that same share, so the realized-FX line
(`both`) is the difference between cash in and income recognized on the
cash basis too.

**A sales receipt** is an invoice (accrual) and a payment (cash) on one
day; nothing special is written, and both views show its income on its
date. **A deposit straight to income** and a register entry are `both`.

## Profit & Loss and Balance Sheet (step 1)

`profit_loss` and `balance_sheet` take `basis`; it reaches
`_totals_by_account(basis=…)`, which runs its one query with the
execution option. The prior-year comparison and the by-month columns run
each period on the same basis. Nothing else in the response changes:
`net_income` under `basis=accrual` or with the parameter omitted (and the
default setting at `accrual`) is the figure of the day before this
branch, byte for byte. `_pl_section` / `_bs_section` and the two CSVs add
"(cash basis)" to the period line only when the basis is cash. The
accountant's package (`package.py`) and the statements PDF stay accrual.

The screen: a Basis select (Company default / Accrual / Cash) in the two
statements' toolbar, riding on the address, the saved report and the
PDF / CSV links like Sort by does; the heading says "cash basis" when it
is.

## Tests to write (step 1)

`tests/test_cash_basis.py`, each through the API and the invariant:

- invoice → partial payment → payment: cash P&L shows each payment's
  share on its date, prorated over two income accounts and sales tax;
  A/R zero on the cash Balance Sheet; the accrual P&L unchanged.
- bill → payment: the mirror, A/P zero.
- void after payment: cash income gone, both views balanced.
- prepayment: Unapplied Cash Payment Income, then applied, moved.
- prior-year compare on the cash basis.
- the backfill on a file written without the column (lines tagged,
  cash lines written once; a second run writes nothing).
- `basis` omitted or `accrual`: the flat report byte-equal to the
  response without the parameter; `report_basis=cash` flips the default.
- the migration: one head, the column, the default, downgrade.
- `test_books_balance_invariants.py` and every existing suite green with
  no edits: the accrual reading is the default everywhere.

## Rollout in PRs

**Step 1 (this branch).** The column and migration; the start-up
backfill; accrual tagging by source type; cash recognition on customer
payments (receive, batch, online, apply, void) and bill payments (pay,
void); `report_basis`; `basis` on the P&L and Balance Sheet, their PDF
and CSV, and the screen toggle; `tests/test_cash_basis.py`.

**Step 2.** The Trial Balance, General Ledger, P&L Detail, account
drill-down, the grids (by Class, by Job, by month) and the nonprofit
statements take `basis`; edits of a paid document recompute its
payments' cash lines; the journal form's basis choice.

**Step 3.** Payroll liabilities and the Sales Tax Liability report on the
cash basis; the retainage follow-up (#552, #555); the accountant's
package on the company default.

## Decisions (2026-10-26, this branch)

1. **The payment's A/R and A/P line is `both`** and the cash pair carries
   its own A/R or A/P line, so each entry balances in every view and the
   raw sum, the stored balances and every unfiltered reader keep their
   figures. (Keith's note had the payment's offset as `accrual`; the net
   effect in the cash view is the one it described — A/R and its offset
   gone — reached with every entry balanced.)
2. **The ledger reads accrual unless asked**, through one session-level
   filter, instead of a `basis` filter at every query site: a reader that
   forgets gets today's figures, never a double-counted income line.
3. **Tagging by source type** inside `create_journal_entry`, so every
   importer and document path tags alike; a line may still say its own
   basis (the cash pair does).
4. **The backfill runs at start**, idempotent, not inside the migration.
5. **4950 / 6990** for the two unapplied-cash accounts, created on demand.
6. **Apply-later reclass lives in the payment's entry**, on the payment's
   date, not in an entry of its own: `test_apply_payment_credit` holds
   that applying a remainder posts no transaction, the period's cash net
   income is unchanged either way, and the void then has one entry to
   reverse. (The text above first had it dated the later of payment and
   invoice, today when closed; dropped for that reason.)
7. **Edits of a paid document leave earlier cash lines alone** (v1).
8. **Independent proration per payment**; a cent may stay on a line.
9. **Payroll and journal entries are `both`** in step 1.
10. **Screen toggle built** in step 1: the toolbar pattern (`_adjOpts`)
    made it a few lines.

## Open items (not settled; conservative choice taken)

- **A link from a cash line to its document** (`recognizes_invoice_id` /
  `recognizes_bill_id`) would let the final payment true up rounding,
  let an edit find the lines to recompute, and give the cash-basis P&L
  Detail a Name column. Not added: step 1 needs none of it.
- **The accountant's package** prints accrual whatever the default. It
  should probably follow `report_basis`; left until step 2 gives it the
  Trial Balance on the same basis.
- **Opening balances through the wizard** post A/R and A/P as `both`; a
  company that starts on the cash basis with open invoices should enter
  them as invoices, which the wizard already suggests.
