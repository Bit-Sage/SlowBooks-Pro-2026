"""Recording a provider payout and matching it to the bank feed (#584).

Step 2 of the clearing-account feature (#583). With a provider's clearing
on, a captured payment waits in the provider's clearing account; the net
payout then lands in the bank and must clear that account, booking any fee
step 1 couldn't. A deposit in the bank review queue can be marked as a
payout (the suggestion), and a payout without a feed is recorded from the
provider's settings card. Both post the same split through the normal bank
entry path, so voids and reconciliation behave as they do for any entry.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.payments.clearing import clearing_account_id, fee_account_id
from app.services.payments.recorder import record_provider_payment
from app.services.settings_service import set_setting


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Payout Test Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="PAY-1001",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("100.00"),
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _enable(db_session, provider):
    set_setting(db_session, f"{provider}_clearing_enabled", "true")
    db_session.commit()


def _make_clearing(db_session, provider):
    """Create the provider's clearing account (so a suggestion has something
    to offer) without a cleared payment. Balance stays 0."""
    clearing_account_id(db_session, provider)
    db_session.commit()


def _net(db_session, account_id):
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .all()
    )
    return sum((ln.debit or Decimal("0")) - (ln.credit or Decimal("0")) for ln in lines)


def _feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deposit_line(db_session, feed, amount, payee):
    bt = BankTransaction(
        bank_account_id=feed["id"],
        date=date(2026, 7, 15),
        amount=Decimal(amount),
        payee=payee,
        description=payee,
        match_status="unmatched",
    )
    db_session.add(bt)
    db_session.commit()
    return bt


def _review_row(client, feed, line_id):
    rows = client.get(f"/api/banking/transactions?bank_account_id={feed['id']}").json()
    return next(r for r in rows if r["id"] == line_id)


# ── The acceptance example: fee unknown at capture ───────────────────────


def test_payout_books_the_fee_when_it_was_unknown_at_capture(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    # Step 1: a 100.00 payment clears with no fee reported yet.
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_584a", Decimal("100.00")
    )
    clearing_id = clearing_account_id(db_session, "stripe")
    assert _net(db_session, clearing_id) == Decimal("100.00")

    # Step 2: the 96.80 net payout lands in the bank.
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    row = _review_row(client, feed, bt.id)
    sp = row["suggested_payout"]
    assert sp and sp["provider"] == "stripe" and sp["display_name"] == "Stripe"
    assert Decimal(str(sp["clearing_balance"])) == Decimal("100.00")

    # The payout route builds the split from the gross: CR clearing 100,
    # DR fee 3.20, DR bank 96.80. The account ids stay server-side.
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "100.00"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["match_status"] == "added"
    assert r.json()["transaction_id"]

    fee_id = fee_account_id(db_session, "stripe")
    bank_id = seed_accounts["1000"].id
    assert _net(db_session, clearing_id) == Decimal("0.00")
    assert _net(db_session, fee_id) == Decimal("3.20")
    assert _net(db_session, bank_id) == Decimal("96.80")


# ── The same, fee already booked at capture ──────────────────────────────


def test_payout_with_no_fee_when_it_was_booked_at_capture(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    # Step 1: the 3.20 fee was known, so clearing already nets to 96.80.
    record_provider_payment(
        db_session,
        "stripe",
        "Stripe",
        invoice.id,
        "cs_584b",
        Decimal("100.00"),
        fee=Decimal("3.20"),
    )
    clearing_id = clearing_account_id(db_session, "stripe")
    fee_id = fee_account_id(db_session, "stripe")
    assert _net(db_session, clearing_id) == Decimal("96.80")
    assert _net(db_session, fee_id) == Decimal("3.20")

    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    row = _review_row(client, feed, bt.id)
    sp = row["suggested_payout"]
    assert Decimal(str(sp["clearing_balance"])) == Decimal("96.80")

    # Gross equals net: one clearing split, no fee.
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "96.80"},
    )
    assert r.status_code == 200, r.text
    txn_id = r.json()["transaction_id"]
    # One clearing split + the bank line.
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn_id)
        .all()
    )
    assert len(lines) == 2
    assert _net(db_session, clearing_id) == Decimal("0.00")
    assert _net(db_session, fee_id) == Decimal("3.20")  # unchanged from step 1
    assert _net(db_session, seed_accounts["1000"].id) == Decimal("96.80")


# ── The suggestion only fires where it should ────────────────────────────


def test_suggestion_only_when_clearing_on(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    _make_clearing(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    assert _review_row(client, feed, bt.id)["suggested_payout"] is not None
    # Clearing off: no suggestion.
    set_setting(db_session, "stripe_clearing_enabled", "false")
    db_session.commit()
    assert _review_row(client, feed, bt.id)["suggested_payout"] is None


def test_suggestion_none_when_no_clearing_account_yet(
    client, db_session, seed_accounts
):
    from app.models.accounts import Account

    _enable(db_session, "stripe")  # on, but nothing has cleared, so no account
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    before = db_session.query(Account).count()
    assert _review_row(client, feed, bt.id)["suggested_payout"] is None
    # Listing the review queue created no account (a read stays a read).
    db_session.expire_all()
    assert db_session.query(Account).count() == before


def test_suggestion_only_for_deposits(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    _make_clearing(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    out = _deposit_line(db_session, feed, "-96.80", "STRIPE PAYOUT")
    assert _review_row(client, feed, out.id)["suggested_payout"] is None


def test_suggestion_only_for_a_matching_payee(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    _make_clearing(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    other = _deposit_line(db_session, feed, "96.80", "ACME HARDWARE REFUND")
    assert _review_row(client, feed, other.id)["suggested_payout"] is None
    # whole-word match: "stripes" is not Stripe
    nonword = _deposit_line(db_session, feed, "50.00", "RACING STRIPES LLC")
    assert _review_row(client, feed, nonword.id)["suggested_payout"] is None


def test_suggestion_names_the_payee_provider(client, db_session, seed_accounts):
    for provider in ("paypal", "square"):
        _enable(db_session, provider)
        _make_clearing(db_session, provider)
    feed = _feed(client, seed_accounts)
    pp = _deposit_line(db_session, feed, "40.00", "PAYPAL TRANSFER")
    sq = _deposit_line(db_session, feed, "60.00", "Square Inc payout")
    assert _review_row(client, feed, pp.id)["suggested_payout"]["provider"] == "paypal"
    assert _review_row(client, feed, sq.id)["suggested_payout"]["provider"] == "square"


# ── Split validation on the Add path ─────────────────────────────────────


def test_splits_and_a_category_are_refused(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    clearing_id = clearing_account_id(db_session, "stripe")
    db_session.commit()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "category_account_id": clearing_id,
            "splits": [{"category_account_id": clearing_id, "amount": "96.80"}],
        },
    )
    assert r.status_code == 400
    assert "not both" in r.json()["detail"]


def test_split_total_must_equal_the_statement_amount(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    clearing_id = clearing_account_id(db_session, "stripe")
    fee_id = fee_account_id(db_session, "stripe")
    db_session.commit()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "splits": [
                {"category_account_id": clearing_id, "amount": "100.00"},
                {"category_account_id": fee_id, "amount": "-5.00"},  # nets to 95.00
            ],
        },
    )
    assert r.status_code == 400
    assert "must agree" in r.json()["detail"]
    assert db_session.get(BankTransaction, bt.id).match_status == "unmatched"


# ── A voided payout restores the clearing balance ────────────────────────


def test_voided_payout_restores_clearing(client, db_session, seed_accounts, invoice):
    _enable(db_session, "stripe")
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_584v", Decimal("100.00")
    )
    clearing_id = clearing_account_id(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "100.00"},
    )
    txn_id = r.json()["transaction_id"]
    assert _net(db_session, clearing_id) == Decimal("0.00")

    v = client.post(f"/api/banking/entries/{txn_id}/void")
    assert v.status_code == 200, v.text
    db_session.expire_all()
    assert _net(db_session, clearing_id) == Decimal("100.00")


# ── The payout route guards ──────────────────────────────────────────────


def test_payout_route_gross_below_net_is_400(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_584g", Decimal("100.00")
    )
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "90.00"},  # below the 96.80 net
    )
    assert r.status_code == 400
    assert "less than the net" in r.json()["detail"]
    assert db_session.get(BankTransaction, bt.id).match_status == "unmatched"


def test_payout_route_clearing_off_is_400(client, db_session, seed_accounts):
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "96.80"},
    )
    assert r.status_code == 400
    assert "clearing is off" in r.json()["detail"]


# ── The manual Record payout endpoint ────────────────────────────────────


def test_manual_payout_happy_path(client, db_session, seed_accounts, invoice):
    _enable(db_session, "stripe")
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_584m", Decimal("100.00")
    )
    clearing_id = clearing_account_id(db_session, "stripe")
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.80",
            "fee": "3.20",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "payout_recorded"
    fee_id = fee_account_id(db_session, "stripe")
    assert _net(db_session, clearing_id) == Decimal("0.00")
    assert _net(db_session, fee_id) == Decimal("3.20")
    assert _net(db_session, seed_accounts["1000"].id) == Decimal("96.80")


def test_manual_payout_clearing_off_is_400(client, db_session, seed_accounts):
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.80",
            "fee": "0",
        },
    )
    assert r.status_code == 400
    assert "clearing is off" in r.json()["detail"]


def test_manual_payout_non_bank_account_is_400(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_584n", Decimal("100.00")
    )
    # 4000 Service Income is not a bank account.
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["4000"].id,
            "net": "96.80",
            "fee": "0",
        },
    )
    assert r.status_code == 400
    assert "bank or credit-card account" in r.json()["detail"]


# ── Review fixes (#586): gross over the balance, refunds, voids, off ─────


def _pay(db_session, invoice, ref, amount="100.00", fee=None, provider="stripe"):
    record_provider_payment(
        db_session,
        provider,
        provider.title(),
        invoice.id,
        ref,
        Decimal(amount),
        fee=Decimal(fee) if fee else None,
    )
    return db_session.query(Payment).filter(Payment.reference == ref).one()


def test_review_route_refuses_a_gross_over_the_clearing_balance(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    _pay(db_session, invoice, "cs_586a", "100.00", fee="3.20")  # clearing holds 96.80
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "50.00", "STRIPE PAYOUT")
    # the old prefill: a payout of 50 with the whole 96.80 as gross books 46.80 as fees
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "200.00"},
    )
    assert r.status_code == 400
    assert "more than Stripe Clearing holds" in r.json()["detail"]
    assert db_session.get(BankTransaction, bt.id).match_status == "unmatched"
    # the net itself is fine
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "50.00"},
    )
    assert r.status_code == 200, r.text


def test_manual_route_refuses_a_gross_over_the_clearing_balance(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    _pay(db_session, invoice, "cs_586b", "100.00", fee="3.20")  # holds 96.80
    body = {
        "date": "2026-07-20",
        "bank_account_id": seed_accounts["1000"].id,
        "net": "96.80",
        "fee": "5.00",
    }
    r = client.post("/api/payments/stripe/payout", json=body)
    assert r.status_code == 400
    assert "more than Stripe Clearing holds" in r.json()["detail"]
    body["fee"] = "0"
    assert client.post("/api/payments/stripe/payout", json=body).status_code == 200


def test_manual_route_quantises_and_names_paypal_properly(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "paypal")
    _pay(db_session, invoice, "pp_586c", "100.00", provider="paypal")
    r = client.post(
        "/api/payments/paypal/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.804",
            "fee": "3.196",
        },
    )
    assert r.status_code == 200, r.text
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert _net(db_session, fee_account_id(db_session, "paypal")) == Decimal("3.20")
    assert _net(db_session, seed_accounts["1000"].id) == Decimal("96.80")
    assert any((ln.description or "").startswith("PayPal payout") for ln in txn.lines)
    assert not any("Paypal" in (ln.description or "") for ln in txn.lines)


def test_late_feed_line_after_a_manual_payout_offers_match_not_payout(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    _pay(db_session, invoice, "cs_586d", "100.00", fee="3.20")
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-01",  # the feed line below lands 14 days later
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.80",
            "fee": "0",
        },
    )
    assert r.status_code == 200, r.text
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    assert _review_row(client, feed, bt.id)["suggested_payout"] is None
    # and the route will not post it a second time
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "96.80"},
    )
    assert r.status_code == 400
    assert "Use Match" in r.json()["detail"]
    cands = client.get(f"/api/banking/transactions/{bt.id}/candidates").json()
    assert len(cands) == 1


def test_void_comes_out_of_clearing_and_keeps_the_fee(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    p = _pay(db_session, invoice, "cs_586e", "100.00", fee="3.20")
    clearing_id = clearing_account_id(db_session, "stripe")
    fee_id = fee_account_id(db_session, "stripe")
    r = client.post(f"/api/payments/{p.id}/void")
    assert r.status_code == 200, r.text
    assert r.json()["warning"] is None
    # the gross left clearing, never touching the bank; the provider kept its fee
    assert _net(db_session, clearing_id) == Decimal("-3.20")
    assert _net(db_session, fee_id) == Decimal("3.20")
    assert _net(db_session, seed_accounts["1000"].id) == Decimal("0")


def test_void_with_fee_refunded_reverses_the_fee_too(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    p = _pay(db_session, invoice, "cs_586f", "100.00", fee="3.20")
    r = client.post(f"/api/payments/{p.id}/void?fee_refunded=true")
    assert r.status_code == 200, r.text
    assert _net(db_session, clearing_account_id(db_session, "stripe")) == Decimal("0")
    assert _net(db_session, fee_account_id(db_session, "stripe")) == Decimal("0")


def test_refund_after_payout_comes_out_of_clearing(
    client, db_session, seed_accounts, invoice
):
    # A refund or dispute after the money was paid out still posts against
    # the provider's clearing account (now short), never the bank.
    _enable(db_session, "stripe")
    p = _pay(db_session, invoice, "cs_586g", "100.00", fee="3.20")
    client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.80",
            "fee": "0",
        },
    )
    bank_before = _net(db_session, seed_accounts["1000"].id)
    r = client.post(f"/api/payments/{p.id}/void")
    assert r.status_code == 200, r.text
    assert _net(db_session, seed_accounts["1000"].id) == bank_before
    # 96.80 held, 96.80 paid out, then the 100 gross reversed (fee lines stay)
    assert _net(db_session, clearing_account_id(db_session, "stripe")) == Decimal(
        "-100.00"
    )


def test_void_of_a_paid_out_payment_warns(client, db_session, seed_accounts, invoice):
    _enable(db_session, "stripe")
    p = _pay(db_session, invoice, "cs_586h", "100.00", fee="3.20")
    client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "96.80",
            "fee": "0",
        },
    )
    r = client.post(f"/api/payments/{p.id}/void")
    assert r.status_code == 200, r.text
    assert "already paid out" in r.json()["warning"]
    assert r.json()["is_voided"] is True  # warned, not blocked


def test_void_of_an_unpaid_out_payment_has_no_warning(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    p = _pay(db_session, invoice, "cs_586i", "100.00")
    r = client.post(f"/api/payments/{p.id}/void")
    assert r.json()["warning"] is None


def test_clearing_off_with_a_balance_warns_and_keeps_payouts(
    client, db_session, seed_accounts, invoice
):
    _enable(db_session, "stripe")
    _pay(db_session, invoice, "cs_586j", "100.00", fee="3.20")  # holds 96.80
    r = client.put("/api/settings", json={"stripe_clearing_enabled": "false"})
    assert r.status_code == 200, r.text
    warnings = r.json()["clearing_warnings"]
    assert len(warnings) == 1 and "Stripe Clearing still holds 96.80" in warnings[0]

    # payouts still work: manual and from the review queue
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "40.00", "STRIPE PAYOUT")
    assert _review_row(client, feed, bt.id)["suggested_payout"] is not None
    r = client.post(
        f"/api/banking/transactions/{bt.id}/payout",
        json={"provider": "stripe", "gross": "40.00"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-20",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "56.80",
            "fee": "0",
        },
    )
    assert r.status_code == 200, r.text

    # new captures skip clearing
    inv2 = Invoice(
        invoice_number="PAY-1002",
        customer_id=invoice.customer_id,
        date=date(2026, 7, 2),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("20.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("20.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("20.00"),
    )
    db_session.add(inv2)
    db_session.commit()
    record_provider_payment(
        db_session, "stripe", "Stripe", inv2.id, "cs_586k", Decimal("20.00")
    )
    assert _net(db_session, clearing_account_id(db_session, "stripe")) == Decimal("0")

    # once the balance is zero, payouts are refused again
    r = client.post(
        "/api/payments/stripe/payout",
        json={
            "date": "2026-07-21",
            "bank_account_id": seed_accounts["1000"].id,
            "net": "1.00",
            "fee": "0",
        },
    )
    assert r.status_code == 400
    assert "clearing is off" in r.json()["detail"]


def test_clearing_off_with_nothing_held_does_not_warn(
    client, db_session, seed_accounts
):
    _enable(db_session, "stripe")
    r = client.put("/api/settings", json={"stripe_clearing_enabled": "false"})
    assert r.status_code == 200, r.text
    assert "clearing_warnings" not in r.json()
