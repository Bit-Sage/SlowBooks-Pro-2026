"""Online-payment clearing accounts and the processing fee (#583).

With a provider's clearing setting on, a captured payment posts DR the
provider's clearing account / CR A/R gross, and the reported fee posts DR the
fee account / CR clearing in the same entry. Off, it keeps posting to
Undeposited Funds exactly as before. Fee extraction from each provider's
payload is covered next to the gross/journal assertions.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import get_ar_account_id, get_undeposited_funds_id
from app.services.payments.clearing import clearing_account_id, fee_account_id
from app.services.payments.recorder import record_provider_payment
from app.services.settings_service import set_setting


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Clearing Test Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="CLR-1001",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("200.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("200.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("200.00"),
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _enable_clearing(db_session, provider):
    set_setting(db_session, f"{provider}_clearing_enabled", "true")
    db_session.commit()


def _lines(db_session, payment):
    txn = db_session.get(Transaction, payment.transaction_id)
    return db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()


def _net(lines, account_id):
    return sum(
        (line.debit or Decimal("0")) - (line.credit or Decimal("0"))
        for line in lines
        if line.account_id == account_id
    )


# ── Clearing OFF: today's behaviour, unchanged ───────────────────────────


def test_clearing_off_posts_to_undeposited_funds(db_session, invoice):
    status = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_off", Decimal("200.00")
    )
    assert status == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="cs_off").one()
    lines = _lines(db_session, payment)
    uf_id = get_undeposited_funds_id(db_session)
    ar_id = get_ar_account_id(db_session)
    assert len(lines) == 2
    assert _net(lines, uf_id) == Decimal("200.00")
    assert _net(lines, ar_id) == Decimal("-200.00")


# ── Clearing ON, with a fee, for each provider ───────────────────────────


@pytest.mark.parametrize(
    "provider,display,ref",
    [("stripe", "Stripe", "cs_on"), ("paypal", "PayPal", "pp_on"), ("square", "Square", "sq_on")],
)
def test_clearing_on_with_fee_posts_gross_and_fee(
    db_session, invoice, provider, display, ref
):
    _enable_clearing(db_session, provider)
    status = record_provider_payment(
        db_session,
        provider,
        display,
        invoice.id,
        ref,
        Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    assert status == "payment_recorded"

    payment = db_session.query(Payment).filter_by(reference=ref).one()
    lines = _lines(db_session, payment)
    ar_id = get_ar_account_id(db_session)
    clearing_id = clearing_account_id(db_session, provider)
    fee_id = fee_account_id(db_session, provider)
    uf_id = get_undeposited_funds_id(db_session)

    # Four lines: gross DR clearing / CR A/R, fee DR fee / CR clearing.
    assert len(lines) == 4
    assert _net(lines, ar_id) == Decimal("-200.00")
    assert _net(lines, clearing_id) == Decimal("193.70")  # gross − fee
    assert _net(lines, fee_id) == Decimal("6.30")
    # Nothing lands in Undeposited Funds.
    assert _net(lines, uf_id) == Decimal("0")
    # The entry balances.
    assert sum(line.debit or 0 for line in lines) == sum(
        line.credit or 0 for line in lines
    )


def test_clearing_on_fee_none_posts_gross_only(db_session, invoice):
    _enable_clearing(db_session, "square")
    status = record_provider_payment(
        db_session, "square", "Square", invoice.id, "sq_nofee", Decimal("200.00"), fee=None
    )
    assert status == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="sq_nofee").one()
    lines = _lines(db_session, payment)
    clearing_id = clearing_account_id(db_session, "square")
    ar_id = get_ar_account_id(db_session)
    assert len(lines) == 2
    assert _net(lines, clearing_id) == Decimal("200.00")
    assert _net(lines, ar_id) == Decimal("-200.00")


def test_clearing_on_zero_fee_posts_gross_only(db_session, invoice):
    _enable_clearing(db_session, "stripe")
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_zero", Decimal("200.00"),
        fee=Decimal("0"),
    )
    payment = db_session.query(Payment).filter_by(reference="cs_zero").one()
    assert len(_lines(db_session, payment)) == 2


def test_fee_posts_in_full_even_when_amount_capped(db_session, invoice):
    _enable_clearing(db_session, "stripe")
    # Overpay: amount caps at the 200 balance, but the reported fee still posts.
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_cap", Decimal("999.99"),
        fee=Decimal("6.30"),
    )
    payment = db_session.query(Payment).filter_by(reference="cs_cap").one()
    assert payment.amount == Decimal("200.00")
    lines = _lines(db_session, payment)
    clearing_id = clearing_account_id(db_session, "stripe")
    fee_id = fee_account_id(db_session, "stripe")
    assert _net(lines, clearing_id) == Decimal("193.70")
    assert _net(lines, fee_id) == Decimal("6.30")


def test_retried_record_with_clearing_records_once(db_session, invoice):
    _enable_clearing(db_session, "stripe")
    first = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_dup", Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    second = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_dup", Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    assert first == "payment_recorded"
    assert second == "already_processed"
    assert db_session.query(Payment).filter_by(reference="cs_dup").count() == 1


# ── Make Deposits excludes a clearing payment ────────────────────────────


def test_make_deposits_excludes_a_clearing_payment(client, db_session, seed_accounts):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider, webhook_signature

    settings = {
        "square_enabled": "true",
        "square_environment": "sandbox",
        "square_access_token": "sq-token",
        "square_location_id": "LOC123",
        "square_webhook_signature_key": "sig-key-abc",
        "square_notification_url": "https://books.example/api/payments/square/webhook",
        "square_clearing_enabled": "true",
    }
    for key, value in settings.items():
        set_setting(db_session, key, value)
    db_session.commit()

    customer = Customer(name="Deposit Exclude", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="CLR-DEP", customer_id=customer.id, date=date(2026, 7, 1),
        status=InvoiceStatus.SENT, subtotal=Decimal("123.45"), tax_rate=Decimal("0"),
        tax_amount=Decimal("0"), total=Decimal("123.45"), amount_paid=Decimal("0"),
        balance_due=Decimal("123.45"), payment_token="tok-clr-dep",
        checkout_provider="square", checkout_external_id="ORDER-DEP",
    )
    db_session.add(inv)
    db_session.commit()

    import json

    body = json.dumps(
        {
            "type": "payment.updated",
            "data": {
                "object": {
                    "payment": {
                        "status": "COMPLETED",
                        "order_id": "ORDER-DEP",
                        "amount_money": {"amount": 12345, "currency": "USD"},
                        "processing_fee": [
                            {"amount_money": {"amount": 388, "currency": "USD"}}
                        ],
                    }
                }
            },
        }
    ).encode()
    sig = webhook_signature(
        settings["square_webhook_signature_key"],
        settings["square_notification_url"],
        body,
    )
    resp = client.post(
        "/api/payments/square/webhook",
        content=body,
        headers={"x-square-hmacsha256-signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_recorded"

    payment = db_session.query(Payment).filter_by(reference="ORDER-DEP").one()
    pending = client.get("/api/deposits/pending").json()
    assert all(row["transaction_id"] != payment.transaction_id for row in pending)
    # And the fee was booked with the gross (verifies the webhook fed the fee).
    lines = _lines(db_session, payment)
    fee_id = fee_account_id(db_session, "square")
    assert _net(lines, fee_id) == Decimal("3.88")


# ── Settings save refuses a non-asset clearing account ───────────────────


def test_settings_save_refuses_non_asset_clearing_account(client, seed_accounts):
    # 4000 Service Income is not an asset account.
    income = client.get("/api/accounts").json()
    acct = next(a for a in income if a["account_number"] == "4000")
    resp = client.put("/api/settings", json={"stripe_clearing_account_id": str(acct["id"])})
    assert resp.status_code == 400
    assert "asset" in resp.json()["detail"].lower()


def test_settings_save_accepts_an_asset_clearing_account(client, seed_accounts):
    accounts = client.get("/api/accounts").json()
    uf = next(a for a in accounts if a["account_number"] == "1200")
    resp = client.put("/api/settings", json={"stripe_clearing_account_id": str(uf["id"])})
    assert resp.status_code == 200


def test_settings_save_refuses_a_bank_feed_clearing_account(client, seed_accounts):
    accounts = client.get("/api/accounts").json()
    checking = next(a for a in accounts if a["account_number"] == "1000")
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(checking["id"])}
    )
    assert resp.status_code == 400


# ── Fee extraction from each provider's payload shape (no network) ───────


def test_stripe_fee_from_balance_transaction(monkeypatch):
    from app.services.payments import stripe as st

    settings = {"stripe_secret_key": "sk_test", "stripe_clearing_enabled": "true"}

    def fake_retrieve(pi_id, expand=None):
        assert pi_id == "pi_123"
        assert expand == ["latest_charge.balance_transaction"]
        return {"latest_charge": {"balance_transaction": {"fee": 630}}}

    monkeypatch.setattr(st.stripe.PaymentIntent, "retrieve", fake_retrieve)
    fee = st.StripeProvider()._maybe_fee({"payment_intent": "pi_123"}, settings)
    assert fee == Decimal("6.30")


def test_stripe_fee_none_when_clearing_off(monkeypatch):
    from app.services.payments import stripe as st

    # retrieve must NOT be called when clearing is off.
    def boom(*a, **k):
        raise AssertionError("should not fetch the fee when clearing is off")

    monkeypatch.setattr(st.stripe.PaymentIntent, "retrieve", boom)
    fee = st.StripeProvider()._maybe_fee(
        {"payment_intent": "pi_123"}, {"stripe_secret_key": "sk"}
    )
    assert fee is None


def test_stripe_fee_none_on_fetch_failure(monkeypatch):
    from app.services.payments import stripe as st

    def boom(*a, **k):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(st.stripe.PaymentIntent, "retrieve", boom)
    fee = st.StripeProvider()._maybe_fee(
        {"payment_intent": "pi_123"},
        {"stripe_secret_key": "sk", "stripe_clearing_enabled": "true"},
    )
    assert fee is None


def test_paypal_capture_fee_parsed():
    from app.services.payments.paypal import _capture_fee

    capture = {
        "seller_receivable_breakdown": {
            "paypal_fee": {"value": "6.30", "currency_code": "USD"},
            "gross_amount": {"value": "200.00"},
        }
    }
    assert _capture_fee(capture) == Decimal("6.30")
    assert _capture_fee({}) is None


def test_paypal_order_fee_summed_across_captures():
    from app.services.payments.paypal import _order_fee

    order = {
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "status": "COMPLETED",
                            "seller_receivable_breakdown": {
                                "paypal_fee": {"value": "6.30"}
                            },
                        }
                    ]
                }
            }
        ]
    }
    assert _order_fee(order) == Decimal("6.30")
    assert _order_fee({"purchase_units": []}) is None


def test_square_processing_fee_summed():
    from app.services.payments.square import processing_fee

    payment = {
        "processing_fee": [
            {"amount_money": {"amount": 300, "currency": "USD"}},
            {"amount_money": {"amount": 88, "currency": "USD"}},
        ]
    }
    assert processing_fee(payment) == Decimal("3.88")
    assert processing_fee({}) is None  # reported late


def test_square_poll_fee_fetches_payment_from_tender(monkeypatch):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider

    settings = {
        "square_access_token": "sq-token",
        "square_environment": "sandbox",
        "square_clearing_enabled": "true",
    }

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"payment": {"processing_fee": [{"amount_money": {"amount": 388}}]}}

    captured = {}

    def fake_send(req, **kw):
        captured["url"] = req["url"]
        return FakeResponse()

    monkeypatch.setattr(sq._http, "send", fake_send)
    order = {"tenders": [{"id": "T1", "payment_id": "PAY99"}]}
    fee = SquareProvider()._poll_fee(order, settings)
    assert fee == Decimal("3.88")
    assert captured["url"].endswith("/v2/payments/PAY99")


def test_square_poll_fee_none_on_failure(monkeypatch):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider

    def boom(req, **kw):
        raise RuntimeError("square down")

    monkeypatch.setattr(sq._http, "send", boom)
    fee = SquareProvider()._poll_fee(
        {"tenders": [{"payment_id": "PAY99"}]},
        {"square_access_token": "t", "square_clearing_enabled": "true"},
    )
    assert fee is None
