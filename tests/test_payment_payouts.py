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
from app.models.transactions import TransactionLine
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
    assert sp and sp["provider"] == "stripe"
    assert Decimal(str(sp["clearing_balance"])) == Decimal("100.00")

    # The modal posts gross 100.00 / fee 3.20: CR clearing 100, DR fee 3.20,
    # DR bank 96.80.
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "payee": "Stripe payout",
            "splits": [
                {"category_account_id": sp["clearing_account_id"], "amount": "100.00"},
                {"category_account_id": sp["fee_account_id"], "amount": "-3.20"},
            ],
        },
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
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "payee": "Stripe payout",
            "splits": [
                {"category_account_id": sp["clearing_account_id"], "amount": "96.80"},
            ],
        },
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
    feed = _feed(client, seed_accounts)
    bt = _deposit_line(db_session, feed, "96.80", "STRIPE PAYOUT")
    # Clearing off: no suggestion.
    assert _review_row(client, feed, bt.id)["suggested_payout"] is None
    _enable(db_session, "stripe")
    assert _review_row(client, feed, bt.id)["suggested_payout"] is not None


def test_suggestion_only_for_deposits(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    out = _deposit_line(db_session, feed, "-96.80", "STRIPE PAYOUT")
    assert _review_row(client, feed, out.id)["suggested_payout"] is None


def test_suggestion_only_for_a_matching_payee(client, db_session, seed_accounts):
    _enable(db_session, "stripe")
    feed = _feed(client, seed_accounts)
    other = _deposit_line(db_session, feed, "96.80", "ACME HARDWARE REFUND")
    assert _review_row(client, feed, other.id)["suggested_payout"] is None
    # whole-word match: "stripes" is not Stripe
    nonword = _deposit_line(db_session, feed, "50.00", "RACING STRIPES LLC")
    assert _review_row(client, feed, nonword.id)["suggested_payout"] is None


def test_suggestion_names_the_payee_provider(client, db_session, seed_accounts):
    for provider in ("paypal", "square"):
        _enable(db_session, provider)
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
    sp = _review_row(client, feed, bt.id)["suggested_payout"]
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "splits": [
                {"category_account_id": sp["clearing_account_id"], "amount": "100.00"},
                {"category_account_id": sp["fee_account_id"], "amount": "-3.20"},
            ],
        },
    )
    txn_id = r.json()["transaction_id"]
    assert _net(db_session, clearing_id) == Decimal("0.00")

    v = client.post(f"/api/banking/entries/{txn_id}/void")
    assert v.status_code == 200, v.text
    db_session.expire_all()
    assert _net(db_session, clearing_id) == Decimal("100.00")


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
