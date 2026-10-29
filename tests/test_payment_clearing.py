"""Online-payment clearing accounts and the processing fee (#583).

With a provider's clearing setting on, a captured payment posts DR the
provider's clearing account / CR A/R gross, and the reported fee posts DR the
fee account / CR clearing in the same entry. Off, it keeps posting to
Undeposited Funds exactly as before. Fee extraction from each provider's
payload is covered next to the gross/journal assertions.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
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
    [
        ("stripe", "Stripe", "cs_on"),
        ("paypal", "PayPal", "pp_on"),
        ("square", "Square", "sq_on"),
    ],
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
        db_session,
        "square",
        "Square",
        invoice.id,
        "sq_nofee",
        Decimal("200.00"),
        fee=None,
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
        db_session,
        "stripe",
        "Stripe",
        invoice.id,
        "cs_zero",
        Decimal("200.00"),
        fee=Decimal("0"),
    )
    payment = db_session.query(Payment).filter_by(reference="cs_zero").one()
    assert len(_lines(db_session, payment)) == 2


def test_capped_overpayment_posts_gross_only_no_fee(db_session, invoice):
    _enable_clearing(db_session, "stripe")
    # Overpay: amount caps at the 200 balance. The reported fee was charged on
    # a larger gross, so booking it would push the clearing balance negative —
    # leave it to the payout step (#584). Gross only.
    record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        invoice.id,
        "cs_cap",
        Decimal("999.99"),
        fee=Decimal("6.30"),
    )
    payment = db_session.query(Payment).filter_by(reference="cs_cap").one()
    assert payment.amount == Decimal("200.00")
    lines = _lines(db_session, payment)
    clearing_id = clearing_account_id(db_session, "stripe")
    assert len(lines) == 2
    assert _net(lines, clearing_id) == Decimal("200.00")


def test_capped_overpayment_with_fee_over_amount_stays_non_negative(
    db_session, seed_accounts
):
    # A small balance overpaid, with a fee larger than the capped amount: the
    # clearing balance must not go negative, so no fee posts.
    customer = Customer(name="Tiny Balance", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="CLR-TINY",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("5.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("5.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("5.00"),
    )
    db_session.add(inv)
    db_session.commit()
    _enable_clearing(db_session, "stripe")
    record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        inv.id,
        "cs_tiny",
        Decimal("999.00"),
        fee=Decimal("6.30"),  # fee > the capped amount of 5.00
    )
    payment = db_session.query(Payment).filter_by(reference="cs_tiny").one()
    assert payment.amount == Decimal("5.00")
    lines = _lines(db_session, payment)
    clearing_id = clearing_account_id(db_session, "stripe")
    assert len(lines) == 2
    assert _net(lines, clearing_id) == Decimal("5.00")


def test_retried_record_with_clearing_records_once(db_session, invoice):
    _enable_clearing(db_session, "stripe")
    first = record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        invoice.id,
        "cs_dup",
        Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    second = record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        invoice.id,
        "cs_dup",
        Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    assert first == "payment_recorded"
    assert second == "already_processed"
    assert db_session.query(Payment).filter_by(reference="cs_dup").count() == 1


# ── Make Deposits excludes a clearing payment ────────────────────────────


def test_make_deposits_excludes_a_clearing_payment(client, db_session, seed_accounts):
    from app.services.payments.square import webhook_signature

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
        invoice_number="CLR-DEP",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("123.45"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("123.45"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("123.45"),
        payment_token="tok-clr-dep",
        checkout_provider="square",
        checkout_external_id="ORDER-DEP",
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


# ── Settings save validates the chosen clearing and fee accounts ─────────


def _make_account(client, name, account_type, number=None):
    body = {"name": name, "account_type": account_type}
    if number:
        body["account_number"] = number
    r = client.post("/api/accounts", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_settings_save_refuses_non_asset_clearing_account(client, seed_accounts):
    # 4000 Service Income is not an asset account.
    income = client.get("/api/accounts").json()
    acct = next(a for a in income if a["account_number"] == "4000")
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(acct["id"])}
    )
    assert resp.status_code == 400
    assert "asset" in resp.json()["detail"].lower()


def test_settings_save_refuses_a_control_clearing_account(client, seed_accounts):
    # 1200 Undeposited Funds is an asset, but a control account the posting
    # code resolves by number — it may not be a clearing account.
    accounts = client.get("/api/accounts").json()
    uf = next(a for a in accounts if a["account_number"] == "1200")
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(uf["id"])}
    )
    assert resp.status_code == 400
    assert "control" in resp.json()["detail"].lower()


def test_settings_save_accepts_an_ordinary_asset_clearing_account(
    client, seed_accounts
):
    acct_id = _make_account(client, "House Clearing", "asset", "1299")
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(acct_id)}
    )
    assert resp.status_code == 200


def test_settings_save_refuses_a_bank_feed_clearing_account(client, seed_accounts):
    accounts = client.get("/api/accounts").json()
    checking = next(a for a in accounts if a["account_number"] == "1000")
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(checking["id"])}
    )
    assert resp.status_code == 400


def test_settings_save_refuses_a_non_expense_fee_account(client, seed_accounts):
    accounts = client.get("/api/accounts").json()
    uf = next(a for a in accounts if a["account_number"] == "1200")
    resp = client.put("/api/settings", json={"stripe_fee_account_id": str(uf["id"])})
    assert resp.status_code == 400
    assert "expense" in resp.json()["detail"].lower()


def test_settings_save_accepts_an_expense_fee_account(client, seed_accounts):
    accounts = client.get("/api/accounts").json()
    fee = next(a for a in accounts if a["account_number"] == "6200")
    resp = client.put("/api/settings", json={"stripe_fee_account_id": str(fee["id"])})
    assert resp.status_code == 200


def test_settings_save_refuses_an_inactive_clearing_account(client, seed_accounts):
    acct_id = _make_account(client, "Retired Clearing", "asset", "1298")
    client.put(f"/api/accounts/{acct_id}", json={"is_active": False})
    resp = client.put(
        "/api/settings", json={"stripe_clearing_account_id": str(acct_id)}
    )
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"].lower()


# ── Fee extraction from each provider's payload shape (no network) ───────


def test_stripe_fee_from_balance_transaction(monkeypatch):
    from app.services.payments import stripe as st

    settings = {"stripe_secret_key": "sk_test", "stripe_clearing_enabled": "true"}

    def fake_retrieve(pi_id, expand=None):
        assert pi_id == "pi_123"
        assert expand == ["latest_charge.balance_transaction"]
        return _stripe_object(
            st.stripe.PaymentIntent,
            {"latest_charge": {"balance_transaction": {"fee": 630}}},
        )

    monkeypatch.setattr(st.stripe.PaymentIntent, "retrieve", fake_retrieve)
    fee = st.StripeProvider()._maybe_fee({"payment_intent": "pi_123"}, settings)
    assert fee == Decimal("6.30")


def _stripe_object(cls, payload: dict):
    """What the SDK really returns: a StripeObject, which since stripe-python
    8 indexes like a dict but is not one (no ``.get``; 2.30.0 gate W-45). A
    dict here would hide that, as it did for the webhook's 500."""
    obj = cls.construct_from(payload, "sk_test")
    return obj


def test_stripe_objects_are_read_as_plain_dicts():
    import stripe

    from app.services.payments.stripe import _plain

    event = _stripe_object(
        stripe.Event,
        {
            "id": "evt_1",
            "object": "event",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "object": "checkout.session",
                    "amount_total": 25000,
                    "metadata": {"invoice_id": "7"},
                    "payment_intent": "pi_1",
                    "line_items": [{"amount": 1}],
                }
            },
        },
    )
    plain = _plain(event)
    session = plain["data"]["object"]
    assert type(session) is dict and type(session["metadata"]) is dict
    assert session.get("amount_total") == 25000
    assert session["metadata"].get("invoice_id") == "7"
    assert session["line_items"] == [{"amount": 1}]
    # a dict (the tests' stubs, an older SDK) comes back as it is
    assert _plain({"a": [1, {"b": None}]}) == {"a": [1, {"b": None}]}


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


def test_paypal_capture_fee_non_numeric_is_none():
    from app.services.payments.paypal import _capture_fee

    # A garbage value raises InvalidOperation (an ArithmeticError) from Decimal.
    capture = {"seller_receivable_breakdown": {"paypal_fee": {"value": "N/A"}}}
    assert _capture_fee(capture) is None


def test_paypal_capture_fee_non_usd_is_none():
    from app.services.payments.paypal import _capture_fee

    capture = {
        "seller_receivable_breakdown": {
            "paypal_fee": {"value": "6.30", "currency_code": "EUR"}
        }
    }
    assert _capture_fee(capture) is None


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


def test_square_processing_fee_skips_non_usd():
    from app.services.payments.square import processing_fee

    payment = {
        "processing_fee": [
            {"amount_money": {"amount": 300, "currency": "USD"}},
            {"amount_money": {"amount": 500, "currency": "EUR"}},
        ]
    }
    # Only the USD fee is summed; the EUR one is left to #584.
    assert processing_fee(payment) == Decimal("3.00")


def _square_poll_settings():
    return {
        "square_access_token": "sq-token",
        "square_environment": "sandbox",
        "square_clearing_enabled": "true",
    }


def test_square_poll_fee_fetches_payment_from_tender(monkeypatch):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"payment": {"processing_fee": [{"amount_money": {"amount": 388}}]}}

    captured = []

    def fake_send(req, **kw):
        captured.append(req["url"])
        return FakeResponse()

    monkeypatch.setattr(sq._http, "send", fake_send)
    order = {"tenders": [{"id": "T1", "payment_id": "PAY99"}]}
    fee = SquareProvider()._poll_fee(order, _square_poll_settings())
    assert fee == Decimal("3.88")
    assert captured == ["https://connect.squareupsandbox.com/v2/payments/PAY99"]


def test_square_poll_fee_sums_across_split_tenders(monkeypatch):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider

    fees = {"PAY1": 300, "PAY2": 88}

    def fake_send(req, **kw):
        pid = req["url"].rsplit("/", 1)[-1]

        class R:
            status_code = 200

            def json(self):
                return {
                    "payment": {
                        "processing_fee": [{"amount_money": {"amount": fees[pid]}}]
                    }
                }

        return R()

    monkeypatch.setattr(sq._http, "send", fake_send)
    order = {"tenders": [{"payment_id": "PAY1"}, {"payment_id": "PAY2"}]}
    assert SquareProvider()._poll_fee(order, _square_poll_settings()) == Decimal("3.88")


def test_square_poll_fee_ignores_tender_id(monkeypatch):
    from app.services.payments import square as sq
    from app.services.payments.square import SquareProvider

    def boom(req, **kw):
        raise AssertionError("must not call a tender id as a payment id")

    monkeypatch.setattr(sq._http, "send", boom)
    # A tender with no payment_id names no payment; its own id is not one.
    order = {"tenders": [{"id": "TENDER-1"}]}
    assert SquareProvider()._poll_fee(order, _square_poll_settings()) is None


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


# ── clearing.py: a stored account id is re-checked at use time ───────────


def test_clearing_account_id_falls_back_when_chosen_is_wrong_type(
    db_session, seed_accounts
):
    from app.services.payments.clearing import clearing_account_id
    from app.services.settings_service import set_setting

    income_id = next(
        a.id for a in db_session.query(Account).all() if a.account_number == "4000"
    )
    set_setting(db_session, "stripe_clearing_account_id", str(income_id))
    db_session.commit()
    resolved = clearing_account_id(db_session, "stripe")
    # Not the income account — the default "Stripe Clearing" instead.
    assert resolved != income_id
    assert db_session.get(Account, resolved).name == "Stripe Clearing"


def test_clearing_account_id_falls_back_when_chosen_deleted(db_session, seed_accounts):
    from app.services.payments.clearing import clearing_account_id
    from app.services.settings_service import set_setting

    set_setting(db_session, "paypal_clearing_account_id", "99999")  # no such account
    db_session.commit()
    resolved = clearing_account_id(db_session, "paypal")
    assert db_session.get(Account, resolved).name == "PayPal Clearing"


def test_fee_account_id_falls_back_when_chosen_is_not_expense(
    db_session, seed_accounts
):
    from app.services.payments.clearing import fee_account_id
    from app.services.settings_service import set_setting

    asset_id = next(
        a.id for a in db_session.query(Account).all() if a.account_number == "1000"
    )
    set_setting(db_session, "square_fee_account_id", str(asset_id))
    db_session.commit()
    resolved = fee_account_id(db_session, "square")
    assert resolved != asset_id
    assert db_session.get(Account, resolved).name == "Merchant Account Fees"


# ── Route-level: fee threaded through webhook and poll paths ─────────────


def _clearing_invoice(db_session, number, token, provider=None, external_id=None):
    customer = Customer(name=f"Route {number}", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number=number,
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("200.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("200.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("200.00"),
        payment_token=token,
        checkout_provider=provider,
        checkout_external_id=external_id,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def test_stripe_webhook_route_posts_fee(client, db_session, seed_accounts, monkeypatch):
    from app.services.payments import stripe as st
    from app.services.settings_service import set_setting

    for k, v in {
        "stripe_enabled": "true",
        "stripe_secret_key": "sk_test",
        "stripe_webhook_secret": "whsec_test",
        "stripe_clearing_enabled": "true",
    }.items():
        set_setting(db_session, k, v)
    db_session.commit()
    inv = _clearing_invoice(db_session, "ST-WH", "tok-st-wh")

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_wh_1",
                "amount_total": 20000,
                "metadata": {"invoice_id": str(inv.id)},
                "payment_intent": "pi_wh_1",
            }
        },
    }
    monkeypatch.setattr(
        st.stripe.Webhook,
        "construct_event",
        lambda payload, sig, secret: _stripe_object(st.stripe.Event, event),
    )
    monkeypatch.setattr(
        st.stripe.PaymentIntent,
        "retrieve",
        lambda pi, expand=None: _stripe_object(
            st.stripe.PaymentIntent,
            {"latest_charge": {"balance_transaction": {"fee": 630}}},
        ),
    )
    resp = client.post(
        "/api/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="cs_wh_1").one()
    lines = _lines(db_session, payment)
    assert len(lines) == 4
    assert _net(lines, fee_account_id(db_session, "stripe")) == Decimal("6.30")
    assert _net(lines, clearing_account_id(db_session, "stripe")) == Decimal("193.70")


def test_stripe_check_status_route_posts_fee(
    client, db_session, seed_accounts, monkeypatch
):
    from app.services.payments import stripe as st
    from app.services.settings_service import set_setting

    for k, v in {
        "stripe_enabled": "true",
        "stripe_secret_key": "sk_test",
        "stripe_clearing_enabled": "true",
    }.items():
        set_setting(db_session, k, v)
    db_session.commit()
    inv = _clearing_invoice(db_session, "ST-POLL", "tok-st-poll", "stripe", "cs_poll_1")

    monkeypatch.setattr(
        st.stripe.checkout.Session,
        "retrieve",
        lambda ext: _stripe_object(
            st.stripe.checkout.Session,
            {
                "id": "cs_poll_1",
                "object": "checkout.session",
                "payment_status": "paid",
                "status": "complete",
                "amount_total": 20000,
                "metadata": {"invoice_id": str(inv.id)},
                "payment_intent": "pi_poll_1",
            },
        ),
    )
    monkeypatch.setattr(
        st.stripe.PaymentIntent,
        "retrieve",
        lambda pi, expand=None: _stripe_object(
            st.stripe.PaymentIntent,
            {"latest_charge": {"balance_transaction": {"fee": 630}}},
        ),
    )
    resp = client.post(f"/api/payments/stripe/check-status/{inv.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="cs_poll_1").one()
    assert len(_lines(db_session, payment)) == 4


def test_check_status_skips_poll_when_already_recorded(
    client, db_session, seed_accounts, monkeypatch
):
    from app.services.payments import stripe as st
    from app.services.settings_service import set_setting

    for k, v in {
        "stripe_enabled": "true",
        "stripe_secret_key": "sk_test",
        "stripe_clearing_enabled": "true",
    }.items():
        set_setting(db_session, k, v)
    db_session.commit()
    inv = _clearing_invoice(db_session, "ST-DUP", "tok-st-dup", "stripe", "cs_dup_1")
    record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        inv.id,
        "cs_dup_1",
        Decimal("200.00"),
        fee=Decimal("6.30"),
    )

    def boom(ext):
        raise AssertionError("poll_status must not run once the payment exists")

    monkeypatch.setattr(st.stripe.checkout.Session, "retrieve", boom)
    resp = client.post(f"/api/payments/stripe/check-status/{inv.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_processed"


def test_paypal_webhook_route_posts_fee(client, db_session, seed_accounts, monkeypatch):
    from app.services.payments import paypal as pp
    from app.services.settings_service import set_setting

    for k, v in {
        "paypal_enabled": "true",
        "paypal_environment": "sandbox",
        "paypal_client_id": "cid",
        "paypal_client_secret": "sec",
        "paypal_webhook_id": "WH-1",
        "paypal_clearing_enabled": "true",
    }.items():
        set_setting(db_session, k, v)
    db_session.commit()
    inv = _clearing_invoice(db_session, "PP-WH", "tok-pp-wh")

    class FakeResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    responses = [
        FakeResponse(200, {"access_token": "TOK", "expires_in": 3600}),
        FakeResponse(200, {"verification_status": "SUCCESS"}),
    ]
    monkeypatch.setattr(pp._http, "send", lambda req, **kw: responses.pop(0))

    import json

    body = json.dumps(
        {
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {
                "id": "CAP-1",
                "amount": {"value": "200.00", "currency_code": "USD"},
                "custom_id": str(inv.id),
                "supplementary_data": {"related_ids": {"order_id": "ORDER-PP"}},
                "seller_receivable_breakdown": {
                    "paypal_fee": {"value": "6.30", "currency_code": "USD"}
                },
            },
        }
    ).encode()
    resp = client.post("/api/payments/paypal/webhook", content=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="ORDER-PP").one()
    lines = _lines(db_session, payment)
    assert len(lines) == 4
    assert _net(lines, fee_account_id(db_session, "paypal")) == Decimal("6.30")


def test_voiding_a_clearing_payment_keeps_the_fee_the_provider_kept(
    client, db_session, seed_accounts
):
    from app.services.settings_service import set_setting

    set_setting(db_session, "stripe_clearing_enabled", "true")
    db_session.commit()
    inv = _clearing_invoice(db_session, "ST-VOID", "tok-st-void")
    record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        inv.id,
        "cs_void_1",
        Decimal("200.00"),
        fee=Decimal("6.30"),
    )
    payment = db_session.query(Payment).filter_by(reference="cs_void_1").one()
    pid = payment.id
    clearing_id = clearing_account_id(db_session, "stripe")
    fee_id = fee_account_id(db_session, "stripe")
    ar_id = get_ar_account_id(db_session)

    resp = client.post(f"/api/payments/{pid}/void")
    assert resp.status_code == 200, resp.text

    # The gross reverses; the fee the provider kept stays as an expense (and
    # clearing runs 6.30 short) unless the provider refunded it (#584).
    def _net_all(account_id):
        rows = (
            db_session.query(TransactionLine)
            .filter(TransactionLine.account_id == account_id)
            .all()
        )
        return _net(rows, account_id)

    db_session.expire_all()
    assert _net_all(clearing_id) == Decimal("-6.30")
    assert _net_all(fee_id) == Decimal("6.30")
    assert _net_all(ar_id) == Decimal("0")
