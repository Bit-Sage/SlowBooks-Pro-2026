"""Invoices from a QuickBooks Desktop Transaction Detail report CSV (#291).

QuickBooks Desktop's own IIF export is lists only, so a Desktop user's
invoices never arrived by IIF, and the report path covered sales receipts,
deposits and checks but not invoices. The same report filtered to Invoice
now imports each invoice with its lines (item, quantity, price, tax), its
Customer:Job and its number; open, partly paid or paid by the report's
Open Balance column, with a payment for what was paid; and the kind is
told from the header row's account (Accounts Receivable for an invoice,
the deposit account for a receipt), so one file can hold both.
"""

from decimal import Decimal
from pathlib import Path

from app.services.qb_report_import import (
    detect_report_type,
    import_invoice_report,
    import_qb_report,
)

FIXTURES = Path(__file__).parent / "fixtures"
PLAIN = (FIXTURES / "qb_transaction_detail_invoices.csv").read_text(encoding="utf-8")
WITH_OPEN = (FIXTURES / "qb_transaction_detail_invoices_open_balance.csv").read_text(
    encoding="utf-8"
)
RECEIPTS = (FIXTURES / "qb_transaction_detail_sales_receipts.csv").read_text(
    encoding="utf-8"
)


def _items(client, seed_accounts):
    for name, rate in (("Exterior door", "400.00"), ("Install labor", "100.00")):
        r = client.post(
            "/api/items",
            json={
                "name": name,
                "item_type": "service",
                "rate": rate,
                "income_account_id": seed_accounts["4000"].id,
            },
        )
        assert r.status_code == 201, r.text


def test_detection_tells_invoices_from_receipts():
    assert detect_report_type(PLAIN) == "invoices"
    assert detect_report_type(WITH_OPEN) == "sales_and_invoices"
    assert detect_report_type(RECEIPTS) == "sales_receipts"


def test_invoices_land_with_their_lines_jobs_and_numbers(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.jobs import Job

    _items(client, seed_accounts)
    result = import_invoice_report(db_session, PLAIN)
    assert result["errors"] == [], result
    assert result["imported"] == 3 and result["open"] == 3
    assert any("no Open Balance column" in w for w in result["warnings"])
    by_num = {i.invoice_number: i for i in db_session.query(Invoice).all()}
    assert set(by_num) == {"1031", "1032", "1033"}
    one = by_num["1031"]
    assert one.customer.name == "Alex Vega"
    job = db_session.get(Job, one.job_id)
    assert (
        job is not None
        and job.name == "Kitchen remodel"
        and job.customer_id == one.customer_id
    )
    lines = sorted(one.lines, key=lambda ln: ln.line_order)
    assert [
        (
            ln.item.name if ln.item else None,
            float(ln.quantity),
            float(ln.rate),
            float(ln.amount),
        )
        for ln in lines
    ] == [
        ("Exterior door", 3.0, 400.0, 1200.0),
        ("Install labor", 1.5, 100.0, 150.0),
    ]
    assert float(one.total) == 1350.0 and one.status == InvoiceStatus.SENT
    assert float(one.balance_due) == 1350.0 and float(one.amount_paid) == 0
    two = by_num["1032"]
    assert (
        float(two.tax_amount) == 37.5
        and float(two.tax_rate) == 0.075
        and float(two.total) == 537.5
    )
    assert not any(i.is_sales_receipt for i in by_num.values())
    # the journals: DR A/R the total, CR income and tax
    for inv in by_num.values():
        assert inv.transaction_id is not None


def test_open_balance_sets_paid_partly_paid_and_open(client, db_session, seed_accounts):
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment, PaymentAllocation
    from app.models.transactions import TransactionLine

    _items(client, seed_accounts)
    result = import_qb_report(db_session, WITH_OPEN)
    assert result["errors"] == [], result
    assert result["detected"] == "sales_and_invoices"
    assert result["invoices"] == 3 and result["sales_receipts"] == 1
    assert (
        result["invoices_paid"],
        result["invoices_partly_paid"],
        result["invoices_open"],
    ) == (1, 1, 1)
    assert not any("no Open Balance" in w for w in result["warnings"])
    by_num = {
        i.invoice_number: i
        for i in db_session.query(Invoice).filter_by(is_sales_receipt=False).all()
    }
    paid, part, open_ = by_num["1031"], by_num["1032"], by_num["1033"]
    assert (
        paid.status == InvoiceStatus.PAID
        and float(paid.balance_due) == 0
        and float(paid.amount_paid) == 1350.0
    )
    assert (
        part.status == InvoiceStatus.PARTIAL
        and float(part.balance_due) == 237.5
        and float(part.amount_paid) == 300.0
    )
    assert open_.status == InvoiceStatus.SENT and float(open_.balance_due) == 80.0
    pays = {a.invoice_id: a for a in db_session.query(PaymentAllocation).all()}
    assert (
        float(pays[paid.id].amount) == 1350.0 and float(pays[part.id].amount) == 300.0
    )
    assert open_.id not in pays
    for p in db_session.query(Payment).all():
        inv_id = next(a.invoice_id for a in pays.values() if a.payment_id == p.id)
        assert p.date == db_session.get(Invoice, inv_id).date
        assert p.deposit_to_account_id == seed_accounts["1200"].id
    # every journal balances
    for t_id in {ln.transaction_id for ln in db_session.query(TransactionLine).all()}:
        rows = db_session.query(TransactionLine).filter_by(transaction_id=t_id).all()
        assert sum(r.debit for r in rows) == sum(r.credit for r in rows)
    # the receipt in the same file came in as a receipt, once
    receipts = db_session.query(Invoice).filter_by(is_sales_receipt=True).all()
    assert len(receipts) == 1 and receipts[0].invoice_number == "7"


def test_a_reupload_skips_what_is_here(client, db_session, seed_accounts):
    _items(client, seed_accounts)
    first = import_qb_report(db_session, WITH_OPEN)
    again = import_qb_report(db_session, WITH_OPEN)
    assert first["invoices"] == 3 and first["sales_receipts"] == 1
    assert again["invoices"] == 0 and again["sales_receipts"] == 0
    assert again["duplicates_skipped"] == 4


def test_the_endpoint_detects_an_invoice_report(client, seed_accounts):
    import io

    _items(client, seed_accounts)
    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("invoices.csv", io.BytesIO(PLAIN.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected"] == "invoices" and body["invoices"] == 3
    assert body["invoices_open"] == 3
    assert body["sales_receipts"] == 0
    assert any("no Open Balance" in w for w in body["warnings"])


def test_the_receipt_importer_leaves_invoice_blocks_alone(db_session, seed_accounts):
    from app.models.invoices import Invoice
    from app.services.qb_report_import import import_sales_receipt_report

    result = import_sales_receipt_report(db_session, WITH_OPEN)
    assert result["imported"] == 1 and result["errors"] == []
    assert db_session.query(Invoice).count() == 1  # the receipt only


def test_a_line_sum_that_misses_the_total_is_refused_whole(db_session, seed_accounts):
    broken = PLAIN.replace('"1,350.00",,0.00,0.00', '"1,400.00",,0.00,0.00', 1)
    result = import_invoice_report(db_session, broken)
    assert result["imported"] == 2
    assert any("Invoice 1031" in e and "do not equal" in e for e in result["errors"])


def test_the_page_names_invoices(client):
    js = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "iif.js"
    ).read_text(encoding="utf-8")
    assert "invoices: 'Invoices'" in js and "sales_and_invoices" in js
    assert "Receipt or Invoice (a receipt imports as a paid sale + payment" in js


def test_decimal_boundaries_hold(db_session, seed_accounts):
    """Amounts quantize to cents and the tax rate keeps its places."""
    result = import_invoice_report(db_session, PLAIN)
    assert result["imported"] == 3
    from app.models.invoices import Invoice

    inv = db_session.query(Invoice).filter_by(invoice_number="1032").one()
    assert inv.tax_rate == Decimal("0.075000") and inv.tax_amount == Decimal("37.50")
