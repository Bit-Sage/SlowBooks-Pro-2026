"""A typed deposit total is capped at Undeposited Funds (#494).

Make Deposits with no payments ticked took `total` as given and posted it,
so a $5,000 figure banked against a single $100 payment waiting to be
deposited. The ticked-payments path already refused a total that disagreed
with what was waiting; the no-ticket path now caps the typed total at the
Undeposited Funds balance the same way.
"""

from app.models.contacts import Customer


def _customer(db_session, name="Acme Diner"):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _receive_payment(client, customer_id, amount, date="2026-09-10"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": date,
            "amount": amount,
            "method": "Check",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _checking_account_id(seed_accounts):
    for acct in seed_accounts.values():
        if acct.bank_kind == "bank":
            return acct.id
    raise AssertionError("no bank account in seed chart")


def test_typed_total_above_undeposited_funds_is_refused(
    client, db_session, seed_accounts
):
    cust = _customer(db_session)
    _receive_payment(client, cust.id, 100)  # only $100 waiting in UF
    bank_id = _checking_account_id(seed_accounts)

    r = client.post(
        "/api/deposits",
        json={"deposit_to_account_id": bank_id, "date": "2026-09-15", "total": 5000},
    )
    assert r.status_code == 400, r.text
    assert "Undeposited" in r.json()["detail"]


def test_typed_total_within_undeposited_funds_posts(client, db_session, seed_accounts):
    cust = _customer(db_session)
    _receive_payment(client, cust.id, 100)
    bank_id = _checking_account_id(seed_accounts)

    r = client.post(
        "/api/deposits",
        json={"deposit_to_account_id": bank_id, "date": "2026-09-15", "total": 100},
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 100.0


def _uf_balance(db_session):
    from decimal import Decimal

    from sqlalchemy import func

    from app.models.transactions import TransactionLine
    from app.services.accounting import get_undeposited_funds_id

    uf_id = get_undeposited_funds_id(db_session)
    debit, credit = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == uf_id)
        .one()
    )
    return Decimal(str(debit)) - Decimal(str(credit))


def _lump(client, bank_id, total):
    return client.post(
        "/api/deposits",
        json={"deposit_to_account_id": bank_id, "date": "2026-09-15", "total": total},
    )


def test_partial_lump_deposit_leaves_only_the_rest(client, db_session, seed_accounts):
    """$100 received, $60 lump-deposited: the $100 item still lists as
    waiting (lump deposits net only against whole items), but only $40 is
    left. A second $100 lump deposit used to post and drive UF to -60."""
    cust = _customer(db_session)
    _receive_payment(client, cust.id, 100)
    bank_id = _checking_account_id(seed_accounts)

    assert _lump(client, bank_id, 60).status_code == 200
    r = _lump(client, bank_id, 100)
    assert r.status_code == 400, r.text
    assert "$40.00" in r.json()["detail"]
    assert _uf_balance(db_session) == 40

    assert _lump(client, bank_id, 40).status_code == 200
    assert _uf_balance(db_session) == 0
    assert _lump(client, bank_id, 0.01).status_code == 400


def test_two_payments_with_prior_deposits(client, db_session, seed_accounts):
    """A ticked deposit of one payment and a partial lump deposit of the
    other: the cap is the live balance, never more."""
    cust = _customer(db_session)
    first = _receive_payment(client, cust.id, 100)
    _receive_payment(client, cust.id, 250, date="2026-09-11")
    bank_id = _checking_account_id(seed_accounts)

    line_id = next(
        row["transaction_line_id"]
        for row in client.get("/api/deposits/pending").json()
        if row["payment_id"] == first["id"]
    )
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": bank_id,
            "date": "2026-09-15",
            "line_ids": [line_id],
        },
    )
    assert r.status_code == 200, r.text
    assert _uf_balance(db_session) == 250

    assert _lump(client, bank_id, 200).status_code == 200
    assert _uf_balance(db_session) == 50
    r = _lump(client, bank_id, 100)
    assert r.status_code == 400, r.text
    assert "$50.00" in r.json()["detail"]
    assert _lump(client, bank_id, 50).status_code == 200
    assert _uf_balance(db_session) == 0
