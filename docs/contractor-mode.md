# Contractor mode

Settings → Company Type → **Construction** sets a company up the way a
contractor keeps its books, the same way **Nonprofit** does for a ministry:
one switch, no change to any data, reversible at any time. It is the
counterpart to QuickBooks Desktop's Contractor Edition. This is Phase 2 (Testing #110) — a
setting, the accounts, the cost codes and the subcontractor vendor type. No
new accounting posts yet.

## The switch

Picking Construction in Settings saves `company_type = construction` and
calls `POST /api/construction/setup`, which:

1. creates the contractor accounts below if they are missing,
2. loads the standard division titles and a set of common sections
   **only when the company has no cost codes of its own** (the same list the
   Cost Codes page's **Load standard divisions and common sections**
   button loads), ensuring the labor / material / subcontract / equipment /
   other cost types first, and
3. seeds the default **trade packages**, mapped to the sections just loaded
   (only when the company has no packages yet).

Setup is idempotent — safe to run again. It never renumbers or renames an
account you already have, never touches an existing cost code (a company
that has built its own cost-code chart gets no standard codes added), and changes
no posting. Switching back to Business leaves the accounts and codes in
place (as switching away from Nonprofit leaves its net-asset accounts); they
are ordinary accounts once created.

## The accounts

Each is created only if an account of that name does not already exist, and
each keeps its suggested number only if the chart has not already used it
(an imported chart that owns 1150 keeps it; the new account takes no
number). Each is pinned to a balance-sheet detail type so it lands on the
right line whatever the number-and-name rule would otherwise guess.

| Number | Name                     | Type      | Detail type            |
| ------ | ------------------------ | --------- | ---------------------- |
| 1150   | Retainage Receivable     | Asset     | Other Current Asset    |
| 1350   | Construction in Progress | Asset     | Other Current Asset    |
| 2050   | Retainage Payable        | Liability | Other Current Liability|
| 2210   | Customer Deposits        | Liability | Other Current Liability|
| 2220   | Use Tax Payable          | Liability | Other Current Liability|
| 5400   | Equipment Cost           | COGS      | Cost of Goods Sold     |
| 5500   | Other Job Costs          | COGS      | Cost of Goods Sold     |

Retainage is the portion of a billing held back until a job is accepted —
receivable when a customer holds it from you, payable when you hold it from
a sub. Construction in Progress carries costs on a job not yet billed.
Customer Deposits holds money taken before work. Use Tax Payable is the use
tax a contractor self-assesses on materials. Equipment Cost and Other Job
Costs are job-cost buckets beside the seed chart's Materials, Labor and
Subcontractor Costs.

## The cost codes

Codes use the **two-level division/section numbering** common in commercial
construction specifications:

- a **division** — a two-digit number and title (`03` Concrete, `26`
  Electrical), the standard division list every US contractor and estimating
  package speaks (the reserved, un-titled division numbers are left out; add
  them yourself); and
- a **section** — `DD SS SS` with an optional `.SS` extension (`03 30 00`
  Cast-in-Place Concrete, `07 42 13.16`), which sits under its division
  through the cost-code tree's parent link, set automatically.

Each code carries a `level` of `division`, `section`, or null for a code that
isn't division/section shaped. **What you type is normalized on save and
import**: `033000`, `03-3000`, `03 3000` and `03.30.00` all become
`03 30 00`. Only a two-digit division or a 6-/8-digit section is reshaped; a
code that numbers its own way (`L-100`, `EQ`, or a numeric `1000`/`2000`
chart) is left exactly as typed, with a null level, and re-importing it never
rewrites it. Everything sorts by code — list, tree, pickers, import result.

The seeded set is the division titles plus a **modest set of common
sections**. The full numbered section catalog is a licensed product, so it is
not shipped; import your own full list through **Import CSV** on the Cost
Codes page — a CSV of just `code,name` is enough (a few hundred sections land
normalized and auto-parented in one call, each section's division created
from the title table if the chart is missing it). Cost type is optional on
import and defaults to `other`.

### Trade packages

A **trade package** (Concrete, Drywall and Ceilings, HVAC, Electrical …) is
what a subcontractor bids and gets awarded; a cost-code **section** is what
costs post to. The two are mapped many-to-many — a package can span
divisions, and a division can be split across packages. Construction setup
seeds a default set of packages mapped to the sections it has; edit them, and
the section mapping, under **Settings → Trade Packages** (shown only for
construction companies). Nothing references a package yet — bids and
commitments are later steps.

### Cost type per line

A cost code carries a **default** cost type, but the type is chosen **per
line** on a budget, estimate, commitment or bill — a `03 30 00` line can post
as subcontract on one job and as labor on another. Leave the line's type
blank to use the code's default, so existing books are unchanged. The picker
shows in construction mode only; a Business or Nonprofit company keeps its
line forms as they were and posts the code's type (the API accepts a line
type for any company). Job-cost reports read the line's type first and fall
back to the code's, and roll a section's cost up under its division.

## Estimating

In construction mode the estimate is a bid, not just a priced list (#551). A
Business or Nonprofit company's estimate form and PDF are exactly as they were.

**Packages and sections.** Each line can belong to a **trade package** — the
bid package a sub is awarded (Concrete, Electrical …) — and to a cost-code
**section** (`03 30 00`), as every costable line does. The package is the
bid's view (lines group under it, Unassigned when blank); the section is where
the cost posts. A unit of measure (SF, LF, EA, CY…) sits beside the quantity.

**Line kinds.** A line is one of four:

- **Base** — ordinary scope. A base line that carries a unit cost is priced
  **at cost** (its rate mirrors the unit cost, so its amount is quantity ×
  unit cost) and its cost is what the markups build on. A base line with no
  unit cost is **priced direct** — its rate is its price, and it is never
  marked up.
- **Allowance** — a budgeted figure for work not yet designed. Its amount is
  in the subtotal and carries no markup unless the estimate's **Markup on
  allowances** flag is on (see below). On accept it budgets its amount as cost
  (a budgeted figure is spent, not margined).
- **Alternate** — an add or deduct the customer can choose. Its amount is in
  no total until it is **accepted**; a deduct carries a negative amount, so it
  subtracts. An alternate that carries a unit cost is shown to the customer at
  its **marked-up price** (cost × the markup factor); one with no cost shows
  its rate price. Accepting it moves it into the scope (at cost, with its
  markups in the markup rows) and adds its cost to the markup base; the
  Alternates block then offers only the ones not yet taken.
- **Unit price** — a rate card (a price per unit for work whose quantity is
  unknown, like rock excavation). It is never in the bid and never seeds a
  budget.

**The lines stay at cost; the markups are their own figures under the
subtotal.** The subtotal is the sum of the included line amounts — base lines
(at cost), accepted alternates and allowances. Beside the subtotal, four
percents are figured on the markup base (the base and accepted-alternate lines
that carry a unit cost, plus the allowances when that flag is on) and **compound in order** — each figured on the
running total before it. Worked example — a $100,000 cost of work, no
allowances, at 8 / 1.5 / 5 / 1 percent:

| Step | Figured on | Amount |
| --- | --- | --- |
| Subtotal | lines at cost | 100,000.00 |
| General conditions (8%) | 100,000 | 8,000.00 |
| Insurance (1.5%) | 108,000 | 1,620.00 |
| Fee (5%) | 109,620 | 5,481.00 |
| Bond (1%) | 115,101 | 1,151.01 |
| **Bid total** | subtotal + the four | **116,252.01** |

The **bid total** is the subtotal plus the markups, and it is **pre-tax**: the
owner's contract sum and schedule of values (a later step, #552) are pre-tax,
and tax is added on each invoice. Tax covers the lines and the markups
together — the tax rate times the taxable line subtotal plus the markups —
when any included line is taxable; an exempt customer pays no tax on anything.
The **Total** is subtotal + markups + tax. The markups are a construction-mode
field: a Business or Nonprofit estimate stores 0 for all four whatever an API
caller sends, so its totals are the plain Subtotal / Tax / Total.

**Markup on allowances is the GC's call, and it is off by default.** Each
estimate carries a `markup_allowances` flag (a checkbox under Markups on the
form). Off, allowances sit in the subtotal at their amount and the markups skip
them. On, the allowance amounts join the markup base, so the four markups are
figured on them too (and the budget revenue split follows). The estimate, the
form's totals panel, the view and the customer PDF all show "Markup on
allowances" as On or Off. The flag freezes with the rest of the price once the
estimate is accepted. There is no separate contingency line kind; a contingency
carried as an allowance line follows the same flag.

**Accept locks the budget.** Accepting an estimate (it needs a job) seeds the
job budget from the bought scope — a budget row you edited by hand still wins —
and then **locks** the seeded rows: they are the contract budget. The lock is
per row: a manual budget row on a code the estimate never touched still saves
after acceptance, but a locked row's own key, or re-seeding the whole budget
from another estimate, is refused. A job whose budget one estimate locked will
not accept a second, and a construction estimate that locked a budget refuses
changes to its lines, markups, customer, job, tax and **status** (its notes and
expiration still edit, and a save that resends the same values is not a
change; moving status off accepted or converted is refused with a 409, since
the next accept would re-seed the locked budget). Business and Nonprofit
estimates lock no budget and edit as they always did. **There
is no unlock** — a new estimate or, later, a change order is how a locked
budget moves. The customer PDF groups the bid by package with the section code,
then blocks the allowances, the alternates not yet taken (at their marked-up
price) and unit prices, and foots Subtotal, a row per non-zero markup, Bid
total, Tax and Total. The seeded budget distributes the markups across the rows'
revenue pro rata to each row's share of the markup base, so the budgeted
revenue foots to the bid. A code you budgeted by hand keeps its own row and its
own share of the markups; that share is not pushed onto the other rows.
**General conditions are both revenue and cost:** the GC percent stays markup
revenue, and on accept the GC amount is also budgeted as a cost on a "GC
General Conditions" code (created if the books have none, reused if they do),
so the job carries it. That row carries cost only; its revenue is already in
the rows above, so it is not counted twice. Insurance, fee and bond are revenue
only. Converting an accepted estimate to an invoice drops
the unit-price lines and any un-accepted alternate, carries each line's cost
type and unit cost onto the invoice, and adds one invoice line per non-zero
markup (on the job) so the invoice foots to the bid.

**Change orders move the locked budget.** A **change order** (#554, see
*Change orders* below) is how a locked budget moves — a numbered document that
revises the contract amount and the budget with an audit trail. Until one is
approved, the lock simply holds: the budget stays as the accepted estimate
left it.

## The contract on the job

An accepted estimate prices the work; the **contract** is the deal with the
owner. A construction job grows a **Contract** tab (construction mode only;
every other company sees the job pages exactly as before) that holds the
terms, a few summary tiles and the owner's schedule of values. A job with no
contract type set behaves the way it always has — the plain Contract amount on
the job form still works for everyone.

**The three types.** A job's contract is a **lump sum** (one fixed price), a
**GMP** — guaranteed maximum price (cost-plus up to a ceiling the owner is
protected by), or **cost-plus** (actual cost plus a fee, optionally with a
not-to-exceed). The type decides how the fee and the cap read:

- **Fee** — the contractor's fee. A **fixed fee** (a dollar amount) wins when
  it is set; otherwise a **fee percent** applies to actual cost on a cost-plus
  or GMP job. A lump-sum job earns no separate fee (it is inside the price).
  The Job Profitability period fee (`fee_period`) is the percent on the
  period's cost; a fixed fee shows none, since it is a job figure.
- **Cap** — the guaranteed maximum on a GMP (normally the contract sum, and
  defaulted to it), or an optional not-to-exceed on cost-plus. *Cap remaining*
  is the cap less cost-to-date and fee.
- **Contingency** — the owner's contingency carried inside the contract sum
  (it rides the schedule as its own line, and building from an estimate adds
  it to the sum so the schedule still balances), drawn down as change orders
  use it (#554 tracks the draw-down).
- **Retainage %** — the portion of each billing the owner holds back until the
  job is accepted. It is stored now and used when progress billing lands.
- **Contract date** and the **source estimate**, shown when the contract was
  built from one.

The summary stamps the **original contract amount** once, the first time a sum
is set with a type (building from an estimate stamps it too, even when the sum
came from the job form). The **contract sum and the contingency edit freely**
(and the original follows the sum) until the job has any billing on the
schedule of values or any approved owner change order (a draft or pending
one, a potential change order or a sub change order does not lock it, and a void
frees it again). From then on the Contract tab and the
job form refuse a different sum or contingency (409 `contract_sum_locked` /
`contingency_locked`), and only an approved change order (#554) moves them, so
the tab shows original + changes = revised. Amounts are rounded to the cent and
the fee percent must be between 0 and 100. Building from an estimate also
copies the estimate's fee markup into the job's fee percent when the job has
none, so the fee tile and the SOV fee line agree on a GMP.

**The schedule of values.** The SOV is the contract sum broken into the lines
an owner pay application bills against — each with an item number, a
description, an optional package and section, a kind (work, allowance,
contingency, or one of the four markups: general conditions, insurance, fee,
bond) and a scheduled value. The sum (and the schedule) is **pre-tax**: sales
tax is added on each invoice, not carried in the contract. **Build from
accepted estimate** lays it out automatically: one **work** line per trade
package (its base lines plus any accepted alternates, **at cost** — the lines
carry no markup), an **allowance** line for each allowance, then one line per
**non-zero markup** in order (general conditions, insurance, fee, bond), each
labelled with its percent, and a **contingency** line when the job carries one.
The work, allowance and markup lines foot exactly to the pre-tax bid. Lines
with no package roll into one "Unassigned" work line. You
can add, remove and reorder rows by hand and edit any value; a value may be
**negative** — a deduct alternate carried onto the schedule nets the sum down.
The SOV must **balance to the contract sum**: a running total and the balance
show at the foot of the grid, and the balance stays highlighted until it is
zero. When the contract sum came from the estimate (the pre-tax bid, plus any
contingency), a freshly built schedule already balances.

**What comes next.** Change orders (#554, see *Change orders* below) revise the
contract amount, the budget and the sub commitments together, with the rule
that work paid for out of an allowance carries no markup. Progress billing
(#555) will turn the SOV
into an owner pay application — previous / this period / stored / retainage /
balance per line — holding retainage back and releasing it at the end. Job
reports (#556) will add the WIP schedule with over- and under-billing. This
issue sets the contract and builds the schedule; it leaves those seams open
rather than filling them.

## Buyout and commitments

The contract is the money coming in; the **buyout** is the money going out — what
the subs and suppliers are committed to. A **commitment is a purchase order**
(#553). There is no new document type: in construction mode a PO is either a
plain **purchase order** or a **subcontract**, chosen with a Type select, and
it can carry a **trade package**, a **retainage %** and **scope notes**. A
subcontract to a vendor not marked a Sub is allowed — the form just notes that
its license and insurance aren't tracked; nothing is blocked.

**The schedule of values is the lines.** A commitment's lines are its schedule
of values: each line's amount is its scheduled value, and the commitment bills
against those lines a period at a time. **Partial billing** — *To Bill* on a
commitment opens the usual convert dialog with an amount-this-period column
prefilled with each line's remaining (scheduled value less what's already
billed). Bill some or all of it: each line tallies its billed-to-date, the
commitment reads **partial** while anything remains and **closed** once every
line is fully billed. A business company's PO bills in one go exactly as before. The dialog takes a
**bill date**, defaulting to today: the bill, its journal entry and the
closing-date check all use it, so a progress bill ages from the day it is
billed and a later period can still be billed once the PO's own month is
closed. (`convert-to-bill` accepts an optional `date`; left out, the PO's date
is used.) Editing the lines of a converted bill keeps each line tied to its
commitment line and re-tallies billed-to-date; a void gives it all back.
A line with no price ($0) is not a bill line — the converted bill carries only
the lines with money on them (a change from the earlier behaviour, where a
description-only line rode along).

**Retainage held.** When the commitment carries a retainage percent, that
percent of each bill's **pre-tax subtotal** is held back — tax is always
payable in full — so the bill credits Accounts Payable for the net and
**Retainage Payable** (2050) for the retainage, its balance due is the net, and
the journal still balances with the gross debits. Pay Bills pays only what's
owed now, and the A/P aging and vendor balances show the net (the held amount
sits on 2050, not A/P). A bill entered by hand can hold retainage the same way.
The 2050 line carries the bill's job, so release can find it by job.
Editing the lines of a converted bill re-computes the retainage at the commitment's rate on the new subtotal, unless the edit sends `retainage_held`. A fully billed (closed) commitment refuses a higher amount (409): raise it with a sub change order.
Releasing retainage (a debit back to 2050) is progress billing's job (#555);
for now the figure is the retainage held to date.

**What committed and billed mean now.** *Committed* cost is the **remaining**
open commitment — the scheduled value of every issued, not-yet-closed
commitment, less what has been billed — so it keeps meaning "ordered but not
yet in the ledger". Beside it the job now carries **committed total** (the
whole of every issued commitment) and **billed to date** (what has been billed
against them), shown as tiles on the job Overview (construction mode) and in the
cost-tree totals; the Overview's "Billed to date (subs)" is the sub/supplier
side and is distinct from the Contract tab's owner "Billed to date". Both are
whole-commitment figures — a commitment has no period — so on a date-ranged
cost tree they are all-time (the response flags this with
`commitments_all_time`), unlike the period-scoped actuals beside them.

**Bid vs buyout.** A **Buyout** tab on the job (after Contract, construction
mode only) pairs each trade package's **bid** — the accepted estimate's cost
for that package — against what has been **bought** (the commitments tagged to
it), with the **variance**, the committed-remaining, the billed-to-date and the
commitments behind each package linked through. Packages bid but not yet bought
show with nothing bought; commitments with no package gather under
*Unassigned*. `GET /api/jobs/{id}/buyout` is the data, and the seam the buyout
report and job-cost-by-vendor (#556) will build on.

**Compliance flags.** Bills and Pay Bills carry each Sub vendor's paperwork —
whether a **W-9** is on file and whether the **certificate of insurance** has
expired (or is missing). The Pay Bills rows and the Buyout tab's commitments
show a compact flag for a Sub that's short either one, and Pay Bills offers a
**lien waiver** status per bill (not required / pending / conditional /
unconditional). A missing W-9 or missing COI is a warning. A Sub whose
certificate has **expired** (by the payment date) is not paid unless the payment
is overridden: Pay Bills asks first, saying why, and the payment records that it
was paid with an expired COI (`coi_override`, with a note naming the expiry),
shown on the bill's payments and the payment's own view. Through the API,
`POST /api/bill-payments` answers 409 until the request carries
`coi_override: true`. The lien waiver is only a status: saving it works on a
bill in a closed period, since it posts nothing.

**What comes next.** Sub **change orders** (#554, see *Change orders* below)
revise a commitment the way owner change orders revise the contract.
**Progress billing** (#555) will turn a commitment's schedule of values into a
sub pay application and release retainage on both sides. **Reports** (#556)
will add the buyout report and job cost by vendor. This issue sets the
commitments and leaves those seams open.

## Change orders

A **change order** is how a locked budget, its contract and its commitments
move, with an audit trail (#554). It comes in three kinds and prices material
and labor separately, each carrying its own markup — except work an allowance
pays for, which carries none.

**The three kinds.**

- A **potential change order (PCO)** prices a change the owner has not yet
  approved. It has no effect on the books; when the owner says yes you
  **promote** it to an owner change order (copying its lines), and the PCO is
  marked *promoted*. Tick several PCOs on one job (same funding, allowance line
  and markup setting) and **Promote selected PCOs** rolls them into **one**
  owner change order: it carries all their lines, its amount is the sum, and
  each PCO links to it. A PCO can be **revised** (*Revise*): the new revision
  (`…-PCO-001-R2`) starts as a draft copy and supersedes the prior one, which is
  kept as history (the log shows the latest; `GET /api/change-orders?job_id=…&history=true`
  shows them all). Only the latest revision can be revised or promoted.

**Numbering.** Change orders are numbered **per job**: the job number, then
`CO-001`, `CO-002`… (`PCO-001`… for PCOs, `SCO-001`… for sub change orders), so
two jobs each start at `CO-001`. A job without a job number uses `J<id>`.
Numbers from before this (`CO-0001`) keep theirs.
- An **owner change order** revises the owner's contract. On approve it moves
  the contract amount, adds a schedule-of-values line and writes the budget.
- A **sub change order** revises a commitment (a purchase order) — it appends
  lines to the sub's scope. A sub change order moves a commitment by **cost**;
  it carries no markup, because the markup is the contractor's and lives on the
  owner side.

**Material and labor priced separately, with markup.** Each line is a cost
type (labor, material, subcontract, equipment, other), a quantity and a unit
cost; its cost is quantity × unit cost. The **markup** that turns cost into the
sell price is the line's own percent, else the change order's, else the job's
**Markup on change orders** contract term, else zero. Labor and material each
carry their own markup, so the two price independently. Worked example — an
owner change order with 10 labor hours at $50 marked up 15% and $1,000 of
material marked up 10%: labor sells at 500 × 1.15 = **$575.00**, material at
1,000 × 1.10 = **$1,100.00**, a change order total of **$1,675.00** on
**$1,500.00** of cost (**$175.00** of markup).

**Markup is off by default when an allowance or contingency pays.** An owner
change order has a **funding**: *contract* (it adds to the contract sum),
*contingency* (it draws the owner's contingency down) or *allowance* (it spends
an allowance line, which must be an allowance line on the same job). Allowance-
and contingency-funded work carries **no markup** by default — the sell equals
the cost — because the money was already held. The contractor can still mark it
up: the change order's **Apply markup** flag turns it on, and the form, the log
(*+ markup*) and the PDF show whether markup was applied. Switching a change
order's funding resets the flag to that funding's default. The same $1,500 of
cost funded by an allowance sells at $1,500 unless markup is applied. A marked-up allowance
change order draws its **sell** price from the allowance (as contingency does),
and the approve limit checks that figure: $2,000 of cost at 15% draws $2,300.
Contract-funded work is always marked up; the flag only governs allowance and
contingency work. A markup
percent must be above -100% and at most 1000%.

**Commitment and budget stay together.** A sub change order made from an owner
change order writes no budget of its own, so it approves only once that owner
change order is approved (409 `owner_co_not_approved`); an owner change order
cannot be rejected or voided while an approved sub change order stands under it
(409 `approved_sub_co`; void the sub CO first). A plain purchase-order edit
cannot drop a line a sub change order added or change its amount, quantity,
rate, job, cost code or cost type (409 `commitment_co_line`); a linked sub change
order cannot raise commitments past its owner change order: at approve, the cost
of the approved sub change orders under it plus this one cannot exceed the owner
change order's cost (409 `owner_co_budget_exceeded`; a rejected or void one does
not count), and a sub change order is still refused on a fully billed
commitment (409 `commitment_not_open`).

**What approve does.** Every effect lands on **approve**, in one transaction,
and reverses on **void**:

- *Owner CO, contract-funded* — adds the sell total to the **contract amount**
  (the original sum is never touched, so the Contract tab reads original +
  changes = revised), appends a read-only **change_order** line to the
  schedule of values for the sell total, and writes locked budget rows
  (`source = change`) with the cost on the cost side and the sell on the
  revenue side. The job-cost tree shows these in its **Changes** column.
- *Owner CO, contingency-funded* — the contract is unchanged; the budget rows
  carry the **cost with no revenue** (the contract already holds the
  contingency money); the draw counts against the contract's contingency
  (refused 409 `contingency_exceeded` when it would pass the contingency
  amount).
- *Owner CO, allowance-funded* — the contract is unchanged and no SOV line is
  added; the budget rows carry the **cost with no revenue** (the allowance
  already holds the money, and the work carries no markup); the draw counts
  against the allowance line (refused 409 `allowance_exceeded` when it would
  pass the allowance). The Contract tab shows each allowance's used and
  remaining.
- *Sub CO* — appends the lines to its commitment, which must be on the **same
  job** and **open** (refused 409 `commitment_job_mismatch` or
  `commitment_not_open` otherwise), and recomputes the commitment's total; the
  buyout table's *bought* grows automatically. The budget rows carry the cost
  with no revenue (and none at all when the sub CO backs an owner CO, which
  already carried that cost).

Because the budget key includes the row's source, a change-order budget row
sits beside an estimate-locked or manual row on the same code and type — the
job-cost tree reads one under **Original** and the other under **Changes**.

A change order's figures **freeze** when it is approved: the cost, sell and
the effective markup each line carried are stored, so changing the job's
markup or a contract term afterward never moves an approved change order, and
void reverses the exact figures it applied.

**Void** reverses each of these — the contract and SOV line, the budget rows,
and the appended commitment lines (exactly the ones it added) — except that a
commitment line already billed against cannot be pulled (409 `co_billed`). The
lifecycle is draft → submitted (*pending*) → approved, with reject and void
beside it; a PCO runs
draft → pending → promoted (asking to *promote* rather than approve).

**What comes next.** Progress billing (#555) will bill the revised schedule of
values — a pay application against the contract sum the change orders moved,
holding and releasing retainage. The change-order **log report** (#556) will
list every change order and its effect. This issue writes the change orders
and leaves those seams open.

## The words

A contractor keeps almost every business word — Customer, Invoice, Estimate,
Job all stay, as they do in QuickBooks' Contractor Edition. The one that
changes is **Subcontractor → Sub**, on screens and in prose. Nothing in the
API, the database or the export formats changes name.

Customer:Job in QuickBooks prints with no space around the colon; SlowBooks
prints "Customer: Job" (with a space) everywhere today, and that is left
unchanged in Phase 2 — see *What this mode does not do yet*.

## The subcontractor vendor type

A vendor can be marked a **Sub** (the UI word; `vendor_type` is
`"sub"`, its only value for now — a Subcontractor). A sub carries:

- **License #** — the trade license number;
- **Insurance Carrier**, **Policy #**, **Insurance Expires** — the
  certificate of insurance a general contractor collects before a sub works.

These fields are blank on an ordinary vendor and reveal only when the vendor
is marked a sub; un-marking a sub clears the license and COI fields but
leaves the 1099 answer as it is. 1099 is off by default for every new vendor
— a lumber yard or the power company is not reportable. **The first time a
vendor is marked a Sub, 1099-NEC turns on** (a sub's labor is reportable);
you can change it afterward, and a sub already set to 1099-MISC keeps it.
W-9 on file is a fact you record, not a default, so it stays No.

## The reports

The Report Center leads with the contractor reports that already exist:
**Job Profitability**, **Job Budget vs Actual** and **P&L by Job**. Committed
Costs and Job Cost by Vendor are not standalone reports yet, so they do not
appear. Every other report stays where it is.

## What this mode does not do yet

- **No first-run prompt.** `company_type` still defaults to Business for a
  new company file; Construction is chosen in Settings afterward, as
  Nonprofit is. A "what kind of company is this?" step at creation is a
  later issue.
- **No draws yet.** No pay applications or progress-billing screens
  (#555); change orders (#554) are in, but the accounts are there to post to
  by hand until progress billing lands.
- **Customer:Job** still prints with a space; the no-space form is deferred.
- **No full section catalog** (the numbered catalog is a licensed product);
  the division titles and a set of common sections ship, and you import the
  rest.
- **The estimate references trade packages** (#551), **the contract on the
  job** (#552) is set with its schedule of values, sub commitments bill
  against a package (#553), and **change orders** (#554, see *Change orders*
  above) revise the contract, budget and commitments; progress billing / owner
  pay applications with retainage (#555) and WIP reports (#556) are still later
  steps.
