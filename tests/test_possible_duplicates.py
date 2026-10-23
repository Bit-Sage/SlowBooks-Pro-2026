"""The Possible Duplicates report (#414): entries on a bank or card account
with the same amount on the same side and the same or a similar payee a
few days apart are grouped; two equal monthly payments are not. A group
can be kept as "not a duplicate", for good; an entry is voided through its
own void route, so the reconciliation and closing-date rules hold. Reading
is every role's; dismissing is a write."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.audit import AuditLog
from app.models.banking import BankAccount, BankTransaction, Reconciliation
from app.models.duplicate_dismissals import DuplicateDismissal
from app.models.users import (
    ROLE_ACCOUNTANT,
    ROLE_BOOKKEEPER,
    ROLE_READONLY,
    User,
)
from app.services import auth as auth_service
from app.services.accounting import create_journal_entry
from app.services.bank_posting import post_bank_entry
from app.services.possible_duplicates import WINDOW_DAYS, similar_payees

URL = "/api/reports/possible-duplicates"
MARCH = "start_date=2026-03-01&end_date=2026-04-30"
OTHER_PW = "other-password-123"


def _keyed(db, accounts, day, amount, payee, number="1000", category="6000"):
    txn = post_bank_entry(
        db,
        accounts[number],
        day,
        Decimal(amount),
        accounts[category],
        payee=payee,
        reference=None,
    )
    db.commit()
    return txn


def _migrated(db, accounts, day, amount, text, number="1000", category="6000"):
    """A journal the QuickBooks Online export import posted."""
    amt = Decimal(amount)
    bank, other = accounts[number].id, accounts[category].id
    lines = (
        [
            {"account_id": other, "debit": -amt, "credit": Decimal("0")},
            {"account_id": bank, "debit": Decimal("0"), "credit": -amt},
        ]
        if amt < 0
        else [
            {"account_id": bank, "debit": amt, "credit": Decimal("0")},
            {"account_id": other, "debit": Decimal("0"), "credit": amt},
        ]
    )
    txn = create_journal_entry(
        db, day, text, lines, source_type="qbo_export_import", reference="J-7"
    )
    db.commit()
    return txn


def _report(client, accounts, number="1000", extra=""):
    r = client.get(f"{URL}?account_id={accounts[number].id}&{MARCH}{extra}")
    assert r.status_code == 200, r.text
    return r.json()


def _user(db, username, role):
    db.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(OTHER_PW),
            role=role,
            is_active=True,
        )
    )
    db.commit()


def _login_as(client, username):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": OTHER_PW}
    )
    assert r.status_code == 200, r.text


# ── payees ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a, b",
    [
        ("Home Depot", "HOME DEPOT"),
        ("Home Depot #4410", "The Home Depot 4410 Orlando"),
        ("Shell Oil 57442", "SHELL OIL 12345"),
        ("Duke Energy, Inc.", "duke energy"),
        ("", ""),
    ],
)
def test_similar_payees(a, b):
    assert similar_payees(a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Shell", "Shellfish Co"),
        ("Home Depot", "Lowe's"),
        ("Depot Home", "Home Depot"),
        ("", "Home Depot"),
    ],
)
def test_different_payees(a, b):
    assert not similar_payees(a, b)


def test_the_window_is_a_few_days():
    assert WINDOW_DAYS == 3


# ── grouping ─────────────────────────────────────────────────────────────


def test_a_keyed_entry_and_its_migrated_copy_are_one_group(
    client, db_session, seed_accounts
):
    keyed = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-120.00", "Home Depot")
    copy = _migrated(
        db_session, seed_accounts, date(2026, 3, 4), "-120.00", "HOME DEPOT #4410"
    )
    data = _report(client, seed_accounts)
    assert data["count"] == 1 and data["window_days"] == 3
    (g,) = data["groups"]
    assert g["side"] == "credit" and g["side_label"] == "Payment"
    assert g["amount"] == 120.0 and g["key"] == f"{keyed.id},{copy.id}"
    by_id = {e["transaction_id"]: e for e in g["entries"]}
    assert by_id[keyed.id]["source"] == "keyed"
    assert by_id[keyed.id]["payee"] == "Home Depot"
    assert by_id[copy.id]["source"] == "migrated"
    assert by_id[copy.id]["reference"] == "J-7"
    assert by_id[keyed.id]["void_path"] == f"/banking/entries/{keyed.id}/void"
    assert by_id[copy.id]["void_path"] == f"/journal/{copy.id}/void"
    assert all(e["voidable"] and not e["reconciled"] for e in g["entries"])
    assert by_id[keyed.id]["link"]


def test_three_copies_a_day_apart_are_one_group(client, db_session, seed_accounts):
    for day in (10, 11, 13):
        _keyed(db_session, seed_accounts, date(2026, 3, day), "-45.00", "Cafe Bleu")
    (g,) = _report(client, seed_accounts)["groups"]
    assert len(g["entries"]) == 3


def test_equal_monthly_payments_are_not_grouped(client, db_session, seed_accounts):
    _keyed(db_session, seed_accounts, date(2026, 3, 1), "-1500.00", "Oak Street Rent")
    _keyed(db_session, seed_accounts, date(2026, 3, 31), "-1500.00", "Oak Street Rent")
    assert _report(client, seed_accounts)["groups"] == []


def test_four_days_apart_is_outside_the_window(client, db_session, seed_accounts):
    _keyed(db_session, seed_accounts, date(2026, 3, 1), "-80.00", "Sparkle Cleaning")
    _keyed(db_session, seed_accounts, date(2026, 3, 5), "-80.00", "Sparkle Cleaning")
    assert _report(client, seed_accounts)["groups"] == []


def test_the_other_side_is_not_a_duplicate(client, db_session, seed_accounts):
    # a refund the same day, for the same amount, from the same shop
    _keyed(db_session, seed_accounts, date(2026, 3, 9), "-60.00", "Staples")
    _keyed(
        db_session, seed_accounts, date(2026, 3, 9), "60.00", "Staples", category="4000"
    )
    assert _report(client, seed_accounts)["groups"] == []


def test_another_payee_or_amount_is_not_a_duplicate(client, db_session, seed_accounts):
    _keyed(db_session, seed_accounts, date(2026, 3, 9), "-60.00", "Staples")
    _keyed(db_session, seed_accounts, date(2026, 3, 9), "-60.00", "Office Depot")
    _keyed(db_session, seed_accounts, date(2026, 3, 10), "-60.01", "Staples")
    assert _report(client, seed_accounts)["groups"] == []


def test_a_voided_copy_drops_out(client, db_session, seed_accounts):
    _keyed(db_session, seed_accounts, date(2026, 3, 9), "-60.00", "Staples")
    second = _keyed(db_session, seed_accounts, date(2026, 3, 9), "-60.00", "Staples")
    assert client.post(f"/api/banking/entries/{second.id}/void").status_code == 200
    assert _report(client, seed_accounts)["groups"] == []


def test_a_voided_payment_drops_out(client, db_session, seed_accounts, seed_customer):
    """A customer payment's void points at the payment, not its posting,
    so the register's voided flag misses it; the report does not."""
    # postings elsewhere first, so a payment's id is not its posting's
    for n in range(5):
        _keyed(db_session, seed_accounts, date(2026, 1, 5), f"-{n + 1}.00", "x", "1010")
    ids = []
    for _ in range(2):
        r = client.post(
            "/api/payments",
            json={
                "customer_id": seed_customer.id,
                "amount": "300.00",
                "date": "2026-03-09",
                "method": "check",
                "deposit_to_account_id": seed_accounts["1000"].id,
                "allocations": [],
            },
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    (g,) = _report(client, seed_accounts)["groups"]
    assert g["side_label"] == "Deposit"
    paths = sorted(e["void_path"] for e in g["entries"])
    assert paths == sorted(f"/payments/{i}/void" for i in ids)
    assert client.post(f"/api{paths[0]}").status_code == 200
    assert _report(client, seed_accounts)["groups"] == []


def test_a_card_reads_charge_and_an_imported_line_says_so(
    client, db_session, seed_accounts
):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 9), "-30.00", "Netflix", "2100")
    _keyed(db_session, seed_accounts, date(2026, 3, 9), "-30.00", "NETFLIX.COM", "2100")
    feed = BankAccount(name="Card feed", account_id=seed_accounts["2100"].id)
    db_session.add(feed)
    db_session.flush()
    card_line = next(ln for ln in a.lines if ln.account_id == seed_accounts["2100"].id)
    db_session.add(
        BankTransaction(
            bank_account_id=feed.id,
            date=date(2026, 3, 9),
            amount=Decimal("-30.00"),
            payee="Netflix",
            transaction_id=a.id,
            transaction_line_id=card_line.id,
            match_status="added",
        )
    )
    db_session.commit()
    (g,) = _report(client, seed_accounts, "2100")["groups"]
    assert g["side_label"] == "Charge"
    sources = {e["transaction_id"]: e["source"] for e in g["entries"]}
    assert sources[a.id] == "imported"
    assert sorted(sources.values()) == ["imported", "keyed"]


def test_only_bank_and_card_accounts(client, seed_accounts):
    r = client.get(f"{URL}?account_id={seed_accounts['6000'].id}&{MARCH}")
    assert r.status_code == 400


# ── not a duplicate ──────────────────────────────────────────────────────


def test_not_a_duplicate_stays_dismissed(client, db_session, seed_accounts):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    b = _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "Pizza Barn")
    r = client.post(
        f"{URL}/dismiss",
        json={"account_id": seed_accounts["1000"].id, "transaction_ids": [b.id, a.id]},
    )
    assert r.status_code == 200, r.text
    kept = db_session.query(DuplicateDismissal).one()
    assert kept.transaction_ids == f"{a.id},{b.id}"
    # audited like any write
    assert (
        db_session.query(AuditLog)
        .filter_by(table_name="duplicate_dismissals", action="INSERT")
        .count()
        == 1
    )

    data = _report(client, seed_accounts)
    assert data["groups"] == [] and data["count"] == 0
    assert data["dismissed_count"] == 1
    shown = _report(client, seed_accounts, extra="&include_dismissed=true")
    (g,) = shown["groups"]
    assert g["dismissed"] and g["dismissal_id"] == kept.id

    # a third copy is news: the group comes back
    c = _keyed(db_session, seed_accounts, date(2026, 3, 4), "-25.00", "Pizza Barn")
    (g,) = _report(client, seed_accounts)["groups"]
    assert {e["transaction_id"] for e in g["entries"]} == {a.id, b.id, c.id}

    # and put back by hand
    r = client.delete(f"{URL}/dismissals/{kept.id}")
    assert r.status_code == 200
    assert db_session.query(DuplicateDismissal).count() == 0


def test_dismiss_takes_entries_on_the_account(client, db_session, seed_accounts):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    b = _keyed(
        db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn", "1010"
    )
    acct = seed_accounts["1000"].id
    r = client.post(
        f"{URL}/dismiss", json={"account_id": acct, "transaction_ids": [a.id, b.id]}
    )
    assert r.status_code == 400
    r = client.post(
        f"{URL}/dismiss", json={"account_id": acct, "transaction_ids": [a.id]}
    )
    assert r.status_code == 400


# ── voiding from the report ──────────────────────────────────────────────


def test_a_reconciled_entry_is_not_offered_and_is_refused(
    client, db_session, seed_accounts
):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-75.00", "Fuel Stop")
    _keyed(db_session, seed_accounts, date(2026, 3, 2), "-75.00", "Fuel Stop")
    recon = Reconciliation(
        account_id=seed_accounts["1000"].id,
        statement_date=date(2026, 3, 31),
        statement_balance=Decimal("0"),
    )
    db_session.add(recon)
    db_session.flush()
    for ln in a.lines:
        if ln.account_id == seed_accounts["1000"].id:
            ln.reconciliation_id = recon.id
            ln.cleared = True
    db_session.commit()

    (g,) = _report(client, seed_accounts)["groups"]
    assert g["any_reconciled"]
    entry = next(e for e in g["entries"] if e["transaction_id"] == a.id)
    assert entry["reconciled"] and not entry["voidable"]
    r = client.post(f"/api{entry['void_path']}")
    assert r.status_code == 400
    assert "reconciliation" in r.json()["detail"]


def test_a_void_in_a_closed_period_asks_for_the_password(
    client, db_session, seed_accounts
):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-75.00", "Fuel Stop")
    b = _migrated(db_session, seed_accounts, date(2026, 3, 2), "-75.00", "Fuel Stop")
    r = client.put(
        "/api/settings",
        json={"closing_date": "2026-03-31", "closing_date_password": "shut-tight"},
    )
    assert r.status_code == 200
    (g,) = _report(client, seed_accounts)["groups"]
    for e in g["entries"]:
        r = client.post(f"/api{e['void_path']}")
        assert r.status_code == 403, (e["source_type"], r.text)
        assert "closing date" in r.json()["detail"]
    # with the password, the keyed copy goes and the group with it
    r = client.post(
        f"/api/banking/entries/{a.id}/void",
        headers={"X-Closing-Date-Password": "shut-tight"},
    )
    assert r.status_code == 200, r.text
    assert _report(client, seed_accounts)["groups"] == []
    assert b.id  # the migrated one stays


# ── roles ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_ACCOUNTANT])
def test_read_only_roles_read_but_do_not_dismiss(
    client, db_session, seed_accounts, role
):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    b = _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "Pizza Barn")
    _user(db_session, "someone", role)
    _login_as(client, "someone")
    data = _report(client, seed_accounts)
    assert data["count"] == 1
    assert (
        client.get(
            f"{URL}/csv?account_id={seed_accounts['1000'].id}&{MARCH}"
        ).status_code
        == 200
    )
    r = client.post(
        f"{URL}/dismiss",
        json={"account_id": seed_accounts["1000"].id, "transaction_ids": [a.id, b.id]},
    )
    assert r.status_code == 403
    assert db_session.query(DuplicateDismissal).count() == 0


def test_a_bookkeeper_dismisses(client, db_session, seed_accounts):
    a = _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    b = _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "Pizza Barn")
    _user(db_session, "keeper", ROLE_BOOKKEEPER)
    _login_as(client, "keeper")
    r = client.post(
        f"{URL}/dismiss",
        json={"account_id": seed_accounts["1000"].id, "transaction_ids": [a.id, b.id]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["dismissed_by"] == "keeper"


# ── Save CSV / Save PDF ──────────────────────────────────────────────────


def test_csv_is_guarded_and_marked(client, db_session, seed_accounts):
    _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "=HYPERLINK(1)")
    _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "=HYPERLINK(1)")
    r = client.get(f"{URL}/csv?account_id={seed_accounts['1000'].id}&{MARCH}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert (
        "possible-duplicates_1000_2026-03-01_2026-04-30.csv"
        in r.headers["content-disposition"]
    )
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")
    assert "Possible Duplicates — 1000 Checking" in text
    assert "'=HYPERLINK(1)" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("1,")]
    assert len(rows) == 2 and all(",Payment,25.00," in ln for ln in rows)


def test_pdf(client, db_session, seed_accounts):
    pytest.importorskip("weasyprint")
    _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "Pizza Barn")
    r = client.get(f"{URL}/pdf?account_id={seed_accounts['1000'].id}&{MARCH}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_a_saved_report_can_carry_it(client, seed_accounts):
    r = client.post(
        "/api/saved-reports",
        json={
            "name": "Checking duplicates",
            "report_type": "possible_duplicates",
            "parameters": {"account_id": seed_accounts["1000"].id},
        },
    )
    assert r.status_code in (200, 201), r.text


def test_the_pdf_section_lists_each_group(client, db_session, seed_accounts):
    """The section the PDF renders, checked where WeasyPrint is missing."""
    from app.services.possible_duplicates import (
        possible_duplicates,
        report_section,
    )

    _keyed(db_session, seed_accounts, date(2026, 3, 2), "-25.00", "Pizza Barn")
    _keyed(db_session, seed_accounts, date(2026, 3, 3), "-25.00", "Pizza Barn")
    data = possible_duplicates(
        db_session, seed_accounts["1000"], date(2026, 3, 1), date(2026, 4, 30)
    )
    section = report_section(data)
    assert section["title"] == "Possible Duplicates — 1000 Checking"
    assert section["columns"][:3] == ["Date", "Number", "Payee"]
    head, *entries = section["rows"]
    assert head["style"] == "subtotal"
    assert head["cells"][0] == "Group 1 · Payment $25.00 · 2 entries"
    assert [r["cells"][2] for r in entries] == ["Pizza Barn", "Pizza Barn"]
