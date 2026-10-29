"""The screens that apply money see every open invoice and bill (issue #191).

GET /api/invoices and /api/bills send the newest 500 by default, and
Batch Payments, Pay Bills, Receive Payment, the credit screens and the
credit-limit check read that page and kept the open ones, so once a
company had more than 500 an old unpaid one never appeared: the oldest,
most overdue, the ones most worth collecting. The lists take open_only,
filtered on the server, and the pages read every page of it. The Invoices
and Bills list pages show the newest 500 and say so, with Show all.
"""

import json
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _many_invoices(db, customer, n=520):
    start = date(2026, 1, 1)
    for i in range(n):
        db.add(
            Invoice(
                invoice_number=f"P-{i:04d}",
                customer_id=customer.id,
                date=start + timedelta(days=i % 200),
                status=InvoiceStatus.PAID,
                subtotal=Decimal("10"),
                total=Decimal("10"),
                amount_paid=Decimal("10"),
                balance_due=Decimal("0"),
            )
        )
    old = Invoice(
        invoice_number="OLD-1",
        customer_id=customer.id,
        date=date(2024, 3, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("75"),
        total=Decimal("75"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("75"),
    )
    db.add(old)
    db.commit()
    return old


def test_an_old_open_invoice_is_listed_open_only(client, db_session):
    c = Customer(name="Tardy Tavern", is_active=True)
    db_session.add(c)
    db_session.commit()
    old = _many_invoices(db_session, c)
    newest = client.get("/api/invoices").json()
    assert len(newest) == 500 and old.id not in {i["id"] for i in newest}
    open_ = client.get("/api/invoices?open_only=true").json()
    assert [i["id"] for i in open_] == [old.id]
    mine = client.get(f"/api/invoices?customer_id={c.id}&open_only=true").json()
    assert [i["invoice_number"] for i in mine] == ["OLD-1"]


def test_an_old_unpaid_bill_is_listed_open_only(client, db_session):
    v = Vendor(name="Cascade Flour Mill", is_active=True)
    db_session.add(v)
    db_session.commit()
    for i in range(510):
        db_session.add(
            Bill(
                bill_number=f"B-{i:04d}",
                vendor_id=v.id,
                date=date(2026, 1, 1) + timedelta(days=i % 200),
                status=BillStatus.PAID,
                subtotal=Decimal("5"),
                total=Decimal("5"),
                amount_paid=Decimal("5"),
                balance_due=Decimal("0"),
            )
        )
    old = Bill(
        bill_number="OLD-B",
        vendor_id=v.id,
        date=date(2024, 5, 1),
        status=BillStatus.UNPAID,
        subtotal=Decimal("40"),
        total=Decimal("40"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("40"),
    )
    db_session.add(old)
    db_session.commit()
    assert old.id not in {b["id"] for b in client.get("/api/bills").json()}
    assert [b["id"] for b in client.get("/api/bills?open_only=true").json()] == [old.id]


def test_every_screen_that_applies_money_reads_every_open_page():
    uses = {
        "batch_payments.js": ["fetchAllPages('/invoices?open_only=true')"],
        "bills.js": ["fetchAllPages('/bills?open_only=true')"],
        "payments.js": [
            "fetchAllPages(`/invoices?customer_id=${customerId}&open_only=true&billed_to=true`)"
        ],
        "credit_memos.js": ["&open_only=true`)"],
        "vendor_credits.js": [
            "fetchAllPages(`/bills?vendor_id=${vc.vendor_id}&open_only=true`)"
        ],
        "invoices.js": [
            "fetchAllPages(`/invoices?customer_id=${encodeURIComponent(customer.id)}&open_only=true`)",
            "listCapNote(rows, 500, 'InvoicesPage.showAll()'",
        ],
    }
    for name, needles in uses.items():
        text = (JS / name).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, (name, needle)
    bills = (JS / "bills.js").read_text(encoding="utf-8")
    assert "API.get('/bills?status=unpaid')" not in bills
    assert "listCapNote(rows, 500, 'BillsPage.showAll()'" in bills


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_fetch_all_pages_reads_until_a_short_page():
    probe = r"""
const fs = require('fs'), vm = require('vm');
const asked = [];
const ctx = { console, window: {}, document: { addEventListener: () => {} },
  API: { get: async (url) => { asked.push(url);
    const skip = Number(/skip=(\d+)/.exec(url)[1]);
    return Array.from({ length: skip < 2000 ? 1000 : skip === 2000 ? 3 : 0 }, (_, i) => skip + i); } } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\nthis.fetchAllPages = fetchAllPages; this.listCapNote = listCapNote;', ctx);
(async () => {
  const rows = await ctx.fetchAllPages('/invoices?open_only=true');
  const note = ctx.listCapNote(new Array(501), 500, 'X.showAll()', 'invoices');
  const none = ctx.listCapNote(new Array(500), 500, 'X.showAll()', 'invoices');
  console.log(JSON.stringify({ n: rows.length, asked, note, none }));
})();
"""
    out = subprocess.run(
        ["node", "-e", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["n"] == 2003
    # it asks until a page comes back empty, so a server that sends fewer
    # than asked for (payroll sends at most 500) is still read to the end
    assert got["asked"] == [
        "/invoices?open_only=true&skip=0&limit=1000",
        "/invoices?open_only=true&skip=1000&limit=1000",
        "/invoices?open_only=true&skip=2000&limit=1000",
        "/invoices?open_only=true&skip=2003&limit=1000",
    ]
    assert "Showing the newest 500 invoices." in got["note"]
    assert "X.showAll()" in got["note"] and got["none"] == ""


def test_every_capped_list_page_says_so_and_offers_show_all():
    pages = {
        "estimates.js": ("EstimatesPage", "'/estimates'"),
        "sales_receipts.js": ("SalesReceiptsPage", "'/sales-receipts'"),
        "credit_memos.js": ("CreditMemosPage", "'/credit-memos'"),
        "purchase_orders.js": ("PurchaseOrdersPage", "'/purchase-orders'"),
        "vendor_credits.js": ("VendorCreditsPage", "'/vendor-credits'"),
        "payments.js": ("PaymentsPage", "'/payments'"),
        "payroll.js": ("PayrollPage", "'/payroll'"),
    }
    for name, (obj, path) in pages.items():
        text = (JS / name).read_text(encoding="utf-8")
        assert f"await listRows({obj}, {path}, '{obj}.showAll()'" in text, name
        assert f"showAll() {{ {obj}._showAll = true;" in text, name
    # the payroll list is capped at 200 by its endpoint
    assert "'pay runs', 200)" in (JS / "payroll.js").read_text(encoding="utf-8")
    # the review queue and a customer's payments are read to the end
    banking = (JS / "banking.js").read_text(encoding="utf-8")
    assert "review = await fetchAllPages(`/banking/transactions?" in banking
    customers = (JS / "customers.js").read_text(encoding="utf-8")
    assert "fetchAllPages(`/payments?customer_id=${id}`)" in customers


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_list_rows_shows_the_newest_and_then_all():
    probe = r"""
const fs = require('fs'), vm = require('vm');
const asked = [];
const rows = (n, from = 0) => Array.from({ length: n }, (_, i) => ({ id: from + i }));
const ctx = { console, window: {}, document: { addEventListener: () => {} },
  API: { get: async (url) => { asked.push(url);
    if (url.includes('limit=501')) return rows(501);
    const skip = Number((/skip=(\d+)/.exec(url) || [0, 0])[1]);
    return skip === 0 ? rows(700) : []; } } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\nthis.listRows = listRows;', ctx);
(async () => {
  const page = {};
  const first = await ctx.listRows(page, '/estimates', 'P.showAll()', 'estimates');
  page._showAll = true;
  const all = await ctx.listRows(page, '/estimates', 'P.showAll()', 'estimates');
  console.log(JSON.stringify({ first: first.rows.length, note: first.note,
    all: all.rows.length, allNote: all.note, flag: page._showAll, asked }));
})();
"""
    out = subprocess.run(
        ["node", "-e", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == 500 and "Showing the newest 500 estimates." in got["note"]
    assert got["all"] == 700 and got["allNote"] == "" and got["flag"] is False
    assert got["asked"][0] == "/estimates?limit=501"
