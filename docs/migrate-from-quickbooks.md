# Migrating from QuickBooks

SlowBooks imports QuickBooks data two ways, depending on which
QuickBooks you're leaving:

| You're on | Use | Where in SlowBooks |
|---|---|---|
| **QuickBooks Online** | Direct API import over OAuth | sidebar → **QuickBooks Online** |
| **QuickBooks Desktop** (2003–current) | IIF file import | sidebar → **QuickBooks Interop** |

Both paths import accounts, customers, vendors, items, invoices,
payments, and **sales receipts**. Sales receipts come across as what
they are underneath: a paid invoice plus its payment, flagged so they
show up on SlowBooks' own Sales Receipts page.

---

## Path 1 — QuickBooks Online (direct API)

One-time setup: connect SlowBooks to your QBO company with OAuth —
follow [setup-qbo.md](setup-qbo.md) (free Intuit developer account,
~10 minutes). Then:

1. Open **QuickBooks Online** in the sidebar.
2. Click **Import All Data**. Entities come over in dependency order
   (accounts → customers → vendors → items → invoices → payments →
   sales receipts), so references resolve as they land.
3. Or use the checkboxes to import entity types selectively — e.g.
   just **Sales Receipts** on a re-run.

Re-runs are safe: every imported record is tracked by its QBO id, and
untracked records are matched by name/number, so nothing imports twice.

---

## Path 2 — QuickBooks Desktop (IIF files)

### What Desktop can export by itself

QuickBooks Desktop's built-in IIF export (`File → Utilities → Export →
Lists to IIF Files`) covers **lists only**: chart of accounts,
customers, vendors, items. Export those and drop the `.iif` file(s) on
**QuickBooks Interop → Import** — there's a **Validate** button that
checks the file before anything is written.

Names typed in ALL CAPS in QuickBooks come across as typed. If you'd rather
have them in normal capitalization, Validate shows a few of the file's names
as they would import and offers **Change ALL-CAPS names to normal
capitalization**. It covers customer, vendor and account names; item names
stay as typed (they are often part numbers), and nothing already in your
books is renamed. Look over the examples first: a name that should stay in
capitals (a company's initials) may not. A transactions file imported later
still finds each name, whichever way it spells it.

### Transactions need one extra step

Desktop does not export transactions to IIF natively. Two options:

- **A third-party IIF transaction exporter** (several exist for QB
  Desktop). The importer understands `INVOICE`, `PAYMENT`,
  `CASH SALE` (sales receipts), `ESTIMATE`, `BILL`, and `DEPOSIT`
  blocks. Import lists first, then transactions, so customer/item/
  account names resolve.
- **For sales receipts, deposits, and checks: a report CSV imports
  directly.** Three Desktop report exports upload straight into
  **QuickBooks Interop → Import from Report CSV** (the report type is
  auto-detected):

  - **Deposit Detail** (`Reports → Banking → Deposit Detail`) — each
    deposit imports as a journal entry moving its payments from
    Undeposited Funds into your bank account, so the check register and
    reconciliation see them.
  - **Check Detail** (`Reports → Banking → Check Detail`) — each check
    imports as a journal entry (bank credit, expense debits). Payroll
    checks work too: withholding lines credit their liability accounts
    and the net matches the check.
  - **Transaction Detail filtered to Sales Receipt** — each receipt
    imports as a paid sale + payment (details below).
  - **Transaction Detail filtered to Invoice** — each invoice imports
    with its lines (item, quantity, price, tax) and its Customer:Job;
    add the **Open Balance** column and each one lands open, partly paid
    or paid, with a payment for what was paid (details below).

  Import your **lists first** (chart of accounts, customers, items via
  IIF) so account and item names resolve — deposit and check blocks
  whose accounts are missing are reported with a pointer to do exactly
  that. All three are safe to re-upload; duplicates are skipped.

  The messy-report trap is exporting a summary report; the sales
  receipt path uses a detail report filtered to one transaction type:

  1. **Reports → Custom Reports → Transaction Detail**
  2. Set the date range to **All**.
  3. **Filters** tab → *Transaction Type* → **Sales Receipt**.
  4. Keep the report's **default columns** — the importer needs at
     least Date, Num, Name, Account, Split, Debit, and Credit (Memo,
     Item, Qty, and Sales Price are used when present).
  5. **Excel → Create New Worksheet**, save as CSV (or Print → Save as
     CSV).
  6. Upload it under **QuickBooks Interop → Sales Receipts from Report
     CSV**. Each receipt imports as a paid sale + its payment, with
     balanced journals; applied-deposit and discount lines keep their
     signs, tax rows are recognized by the percentage in Sales Price,
     and re-uploads skip duplicates.

  Import your **lists first** (chart of accounts, customers, items via
  IIF) so the report's account and item names resolve — unmatched
  accounts post to your default income account, with a warning naming
  them.

### Invoices from a report CSV

The same report, filtered to **Invoice** instead of Sales Receipt:

1. **Reports → Custom Reports → Transaction Detail**, date range **All**.
2. **Filters** tab → *Transaction Type* → **Invoice**.
3. **Customize Report → Display → Columns**: keep the defaults and add
   **Open Balance** (and **Due Date** if you want due dates). Without
   Open Balance the importer cannot tell a paid invoice from an open one,
   so every invoice comes in open and the import says how many did; upload
   the report again with the column and the invoices here that have
   nothing paid on them are marked paid or partly paid from it.
4. Excel → Create New Worksheet, save as CSV, and upload it under
   **QuickBooks Interop → Import from Report CSV**. The report type is
   detected from the rows: an invoice's header row posts to Accounts
   Receivable, a receipt's to the deposit account, so one file can hold
   both.

Each invoice imports with its item lines (item, quantity, unit price, from
the Item, Qty and Sales Price columns), its sales tax from the percentage
row, its Customer:Job (the job is created under the customer on first
sight), and its number. With Open Balance: **0.00** lands as **paid**, with
a payment for the total dated the invoice's date, deposited to Undeposited
Funds; between zero and the total lands **partly paid**, with a payment for
the paid part; equal to the total lands **open**. Re-uploads skip an
invoice already here: the same customer's under the report's number, as it
was numbered here or as the import renumbered it when that number was
taken (`QB-1031`), or, unnumbered, the customer's invoice of that date and
total. The
payments QuickBooks recorded are not in this report, so an imported paid
invoice carries one payment dated the invoice; a Deposit Detail import
then moves that money from Undeposited Funds to the bank.

### How CASH SALE blocks import

QuickBooks writes each sales receipt as a `CASH SALE` transaction
block: the header line carries the deposit account (bank or Undeposited
Funds), the split lines carry income and sales-tax credits. SlowBooks
imports each one as:

- an **invoice** flagged as a sales receipt, status **Paid**, with the
  split lines as line items (splits against accounts containing "tax"
  become the tax amount);
- a **payment** for the full total, deposited to the header account
  (falling back to Undeposited Funds if the account name doesn't
  match);
- balanced journal entries for both, so the ledger nets to
  cash/Undeposited Funds + income — identical to a receipt entered on
  the Sales Receipts screen.

Details worth knowing:

- **Customer:Job names** — the colon splits the customer from the job: `Smith:Kitchen Remodel` becomes customer *Smith* with job *Kitchen Remodel*, and the invoice, sales receipt or estimate is tagged to that job (see the Jobs page). A deeper `A:B:C` path becomes job `B:C` under `A`.
- **Blank customer** — QB allows counter sales with no Customer:Job.
  Those import against an auto-created **"Walk-In Customer"** (the
  import report notes each one).
- **Unnumbered receipts** — receipts without a `DOCNUM` get the next
  SlowBooks invoice number. Numbered receipts keep their numbers.
- **Duplicates** — re-importing the same file skips receipts whose
  document number already exists (or, for unnumbered ones, an existing
  receipt with the same customer + date + total).

---

## After the import

- Spot-check **Reports → Trial Balance** against the same report in
  QuickBooks as of your cutover date (mind the date range — set it to
  cover the imported history).
- Sales receipts are listed on **Sales Receipts** in the sidebar;
  regular invoices stay on **Invoices**.
- Going forward, enter point-of-sale style sales on the **Sales
  Receipts** screen — one form records the sale and the payment
  together.
