# Working with your accountant

The question behind this page: *"My accountant reviews our books in
QuickBooks. How do I send them the QBX and apply the QBY when it comes
back?"*

**Short answer: you can't, and with SlowBooks you don't need to.** QBX and
QBY are Intuit's own Accountant's Copy formats. Only QuickBooks Desktop can
make or apply them, so SlowBooks doesn't read or write them. The Accountant's
Copy exists so you and your accountant can work on separate copies at the
same time and merge their changes back. SlowBooks gives you the same outcome
its own way. There are three routes, from the most direct down.

## 1. Your accountant signs in (Server Edition)

Server Edition is the same download, running as a server so everyone uses a
browser ([server-edition.md](server-edition.md)). **Invite your accountant**
under **Settings → Users**: the sign-in gets the **Accountant** role, a
one-time password you send once with the address, and an end date if the
engagement has one; they choose their own password at the first sign-in.
What the role can do:

- they read everything, the reports, every list and the **Audit Log**
  included, so they can see exactly what moved and who moved it;
- they post **journal entries** and void the ones they posted, and nothing
  else: an invoice, a bill, a payment, a bank entry, a setting or a user is
  refused to them with a sentence that says so;
- payroll and HR stay the administrator's, reads included, as they are for
  every role but admin.

Their entries post as **adjusting entries**: marked on the entry and on the
Journal Entries list (where *Show: Adjusting entries only* lists them alone),
with the name of whoever posted each one. Reports → **Adjusting Journal
Entries** lists them for a period, as PDF or CSV, and the Profit & Loss and
Trial Balance can be run *without* them, for a before-and-after view.

Set a **Closing Date** (Settings → Closing Date, with its own password) once
they have signed off a period, the way QuickBooks' closing date works: a
change dated into the closed period then takes the password, and each use
is written to the audit log.

If your accountant is not on your network, the setup most people use is a
Cloudflare Tunnel from the host with Cloudflare Access in front of it, so
only the email addresses you approve get through, switched on while they
need it ([cloud-hosting.md](cloud-hosting.md)). Nothing on your network is
opened to the internet that way.

## 2. Send them the reports

If they would rather not sign in, send:

- Reports → **Financial Statements Pack**: the Profit & Loss, Balance Sheet
  and Trial Balance in one PDF;
- Reports → **General Ledger** → Save CSV: every line, by account;
- the A/R and A/P agings, and the bank reconciliation reports, as needed.

Then enter their adjustments yourself under **Journal Entries**, with the
**Adjusting entry** box ticked, so they show on the Adjusting Journal Entries
report like anyone else's.

## 3. They insist on QuickBooks Desktop

Interoperability → **Export to IIF** carries the whole ledger into
QuickBooks Desktop: the lists, invoices, sales receipts, payments, bills,
deposits, checks, card charges, transfers, bill payments and journal entries
([migrate-from-quickbooks.md](migrate-from-quickbooks.md) describes the
file). Their adjustments come back two ways: an IIF with GENERAL JOURNAL
blocks imports them as journal entries (tick *This is an accountant's file*
to mark them adjusting), and QuickBooks Desktop's Transaction Detail by
Account report, filtered to Transaction Type = Journal, imports under
**Adjusting entries only**, which refuses the file whole if it holds anything
else, so a whole-ledger upload can never double the books. A second upload
of either posts nothing twice.

## What this page will grow into

The rest of the accountant review this release started on: a Closing Date
Exceptions report, a one-click accountant's package, the full QuickBooks
Desktop round trip, inviting an accountant with an end date, and SlowBooks'
own send-adjust-apply Accountant's Copy with no server at all. Each lands
here as it ships.
