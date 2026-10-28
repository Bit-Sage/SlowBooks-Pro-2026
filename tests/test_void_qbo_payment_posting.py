"""Voiding the journal entry of a payment the QuickBooks Online ledger import
posted voids the payment with it (journal.void_journal_entry). Since #584 put
`fee_refunded` second in `void_payment`'s signature, the route's positional
`void_payment(document.id, db)` handed the session to `fee_refunded` and left
`db` as the `Depends` default: a 500 instead of a void."""

from app.models.payments import Payment
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction


def _paid_invoice(client, customer_id):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-03-01",
            "lines": [{"description": "Work", "quantity": 1, "rate": 100}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-03-05",
            "amount": 100.0,
            "method": "check",
            "allocations": [{"invoice_id": inv.json()["id"], "amount": 100.0}],
        },
    )
    assert pay.status_code in (200, 201), pay.text
    return pay.json()["id"]


def test_voiding_an_imported_payments_posting_voids_the_payment(
    client, db_session, seed_accounts, seed_customer
):
    payment = db_session.get(Payment, _paid_invoice(client, seed_customer.id))
    txn = db_session.get(Transaction, payment.transaction_id)
    # dress the payment's entry as the ledger import's posting of it
    txn.source_type = "qbo_ledger"
    txn.source_id = None
    db_session.add(
        QBOMapping(entity_type="payment", slowbooks_id=payment.id, qbo_id="7001")
    )
    db_session.add(
        QBOMapping(entity_type="ledger", slowbooks_id=txn.id, qbo_id="Payment:7001")
    )
    db_session.commit()

    r = client.post(f"/api/journal/{txn.id}/void")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Payment, payment.id).is_voided
