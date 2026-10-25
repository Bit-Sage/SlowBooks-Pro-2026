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
- **No new accounting.** No change orders, draws (AIA-style pay
  applications) or progress-billing screens; the accounts are there to post
  to by hand until those land.
- **No COI-expiry warning** in Pay Bills — a later issue.
- **Customer:Job** still prints with a space; the no-space form is deferred.
- **No full section catalog** (the numbered catalog is a licensed product);
  the division titles and a set of common sections ship, and you import the
  rest.
- **Nothing references a trade package yet** — bids and commitments against a
  package are later steps.
