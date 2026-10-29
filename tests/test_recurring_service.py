"""Tests for the recurring invoice generator.

The drift case (qty=1.5, rate=33.33 → 49.995 per line) exposes a rounding
mismatch: the service computes the journal entry from unrounded Decimals
that balance in-memory, but each TransactionLine.credit column rounds
49.995 -> 50.00 while A/R debit rounds the sum 99.99 -> 99.99. Once stored,
debits != credits and the invoice's stored subtotal disagrees with the sum
of its stored line amounts.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.invoices import Invoice, InvoiceLine
from app.models.transactions import TransactionLine
from app.services import scheduler
from app.services.recurring_service import generate_due_invoices


def _make_recurring(db_session, customer_id, lines, tax_rate=Decimal("0")):
    rec = RecurringInvoice(
        customer_id=customer_id,
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_due=date(2026, 1, 1),
        is_active=True,
        terms="Net 30",
        tax_rate=tax_rate,
    )
    db_session.add(rec)
    db_session.flush()
    for i, (qty, rate, desc) in enumerate(lines):
        db_session.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                quantity=qty,
                rate=rate,
                description=desc,
                line_order=i,
            )
        )
    db_session.commit()
    return rec


def _sum_debits_credits(db_session, txn_id):
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    return dr, cr


def test_simple_recurring_invoice_balances(db_session, seed_accounts, seed_customer):
    _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("100.00"), "Service")],
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    assert len(ids) == 1

    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()
    assert invoice.total == Decimal("100.00")
    assert invoice.balance_due == Decimal("100.00")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("100.00")


def test_recurring_invoice_with_tax_balances(db_session, seed_accounts, seed_customer):
    _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("100.00"), "Service")],
        tax_rate=Decimal("0.0875"),
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("108.75")


def test_subcent_line_amount_keeps_journal_balanced(
    db_session, seed_accounts, seed_customer
):
    """qty=1.5, rate=33.33 → 49.995 per line. Two lines.

    Before the fix: each line credit stored as 50.00, A/R debit stored as
    99.99 → unbalanced after persistence. After the fix: lines round to
    49.99 each pre-sum, so both sides land at 99.98.
    """
    _make_recurring(
        db_session,
        seed_customer.id,
        [
            (Decimal("1.5"), Decimal("33.33"), "Half rate A"),
            (Decimal("1.5"), Decimal("33.33"), "Half rate B"),
        ],
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    # Reload from DB to see what was actually persisted (catches in-memory
    # vs stored drift).
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    line_amounts = db_session.query(InvoiceLine).filter_by(invoice_id=invoice.id).all()
    sum_of_stored_lines = sum(
        (Decimal(str(l.amount)) for l in line_amounts), Decimal("0")
    )

    # Stored subtotal must equal the sum of stored line amounts. Pre-fix this
    # fails: subtotal stores as 99.99 while line amounts each store as 50.00
    # (sum 100.00).
    assert (
        invoice.subtotal == sum_of_stored_lines
    ), f"subtotal {invoice.subtotal} != sum of line amounts {sum_of_stored_lines}"
    assert invoice.total == invoice.balance_due

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr, f"Journal entry unbalanced: debits={dr} credits={cr}"


def test_end_date_deactivates_recurring(db_session, seed_accounts, seed_customer):
    rec = _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("50.00"), "Service")],
    )
    rec.end_date = date(2026, 1, 15)
    db_session.commit()

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    assert len(ids) == 1  # First invoice generates on 2026-01-01

    db_session.expire_all()
    rec = db_session.query(RecurringInvoice).filter_by(id=rec.id).first()
    # next_due advanced to 2026-02-01, past end_date → deactivated
    assert rec.is_active is False
    assert rec.invoices_created == 1


def test_next_due_advances_by_frequency(db_session, seed_accounts, seed_customer):
    rec = _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("50.00"), "Service")],
    )
    rec.frequency = "quarterly"
    db_session.commit()

    generate_due_invoices(db_session, as_of=date(2026, 1, 1))

    db_session.expire_all()
    rec = db_session.query(RecurringInvoice).filter_by(id=rec.id).first()
    assert rec.next_due == date(2026, 4, 1)


# ---------------------------------------------------------------------------
# 2.31.1 gate W-85: a schedule on the 29th to the 31st keeps its day
# ---------------------------------------------------------------------------


def _schedule_from(db_session, customer_id, start, frequency, next_due=None):
    rec = RecurringInvoice(
        customer_id=customer_id,
        frequency=frequency,
        start_date=start,
        next_due=next_due or start,
        is_active=True,
        terms="Net 30",
        tax_rate=Decimal("0"),
    )
    db_session.add(rec)
    db_session.flush()
    db_session.add(
        RecurringInvoiceLine(
            recurring_invoice_id=rec.id,
            quantity=Decimal("1"),
            rate=Decimal("100.00"),
            description="Service",
            line_order=0,
        )
    )
    db_session.commit()
    return rec


def _dates_made(db_session, rec_id):
    return [
        inv.date
        for inv in db_session.query(Invoice)
        .filter_by(recurring_invoice_id=rec_id)
        .order_by(Invoice.date)
        .all()
    ]


def _by_hand(start: date, months: int) -> date:
    """The installment `months` after `start`, worked out without the
    generator: the start day, cut back to the last day of a shorter month."""
    year, month0 = divmod(start.month - 1 + months, 12)
    year += start.year
    last = calendar.monthrange(year, month0 + 1)[1]
    return date(year, month0 + 1, min(start.day, last))


def test_a_schedule_on_the_31st_keeps_its_day(db_session, seed_accounts, seed_customer):
    """W-85: monthly from January 31 went 01-31, 02-28 and then the 28th of
    every month for good. It lands on the last day of a shorter month and on
    the 31st again after it."""
    rec = _schedule_from(db_session, seed_customer.id, date(2026, 1, 31), "monthly")
    ids, skipped = scheduler.catch_up_recurring(db_session, date(2026, 5, 31))
    assert len(ids) == 5 and skipped == []
    assert _dates_made(db_session, rec.id) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]
    db_session.expire_all()
    rec = db_session.get(RecurringInvoice, rec.id)
    assert rec.next_due == date(2026, 6, 30) and rec.invoices_created == 5


@pytest.mark.parametrize(
    "start, frequency, months, as_of",
    [
        # the run on 10-29 made October's on the 28th: 10 where 9 are due
        (date(2026, 1, 31), "monthly", 1, date(2026, 10, 29)),
        (date(2026, 1, 30), "monthly", 1, date(2026, 12, 29)),
        (date(2026, 1, 29), "monthly", 1, date(2027, 3, 28)),
        (date(2025, 11, 30), "quarterly", 3, date(2026, 11, 29)),
        (date(2024, 2, 29), "yearly", 12, date(2028, 2, 28)),
    ],
)
def test_a_catch_up_makes_the_installments_a_count_by_hand_says(
    db_session, seed_accounts, seed_customer, start, frequency, months, as_of
):
    """W-85: a catch-up over a range makes exactly the installments worked
    out by hand from the start day, each dated its day (the 28th of
    February, the 31st again in March), and leaves the next one due; the
    pledge report counts the same installments the generator makes."""
    from app.services.pledge_report import scheduled_installments

    expected = []
    k = 0
    while _by_hand(start, k * months) <= as_of:
        expected.append(_by_hand(start, k * months))
        k += 1
    rec = _schedule_from(db_session, seed_customer.id, start, frequency)
    ids, _ = scheduler.catch_up_recurring(db_session, as_of)
    assert _dates_made(db_session, rec.id) == expected
    assert len(ids) == len(expected)
    db_session.expire_all()
    rec = db_session.get(RecurringInvoice, rec.id)
    assert rec.next_due == _by_hand(start, k * months) > as_of
    assert scheduled_installments(rec, start, as_of) == len(expected)


def test_a_schedule_that_slipped_returns_to_its_day(
    db_session, seed_accounts, seed_customer
):
    """W-85: a schedule an earlier release left on the 28th (begun on the
    31st) is generated for the date it shows, then goes back to its day."""
    rec = _schedule_from(
        db_session,
        seed_customer.id,
        date(2026, 1, 31),
        "monthly",
        next_due=date(2026, 3, 28),
    )
    scheduler.catch_up_recurring(db_session, date(2026, 5, 31))
    assert _dates_made(db_session, rec.id) == [
        date(2026, 3, 28),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]
    db_session.expire_all()
    assert db_session.get(RecurringInvoice, rec.id).next_due == date(2026, 6, 30)


def test_twice_a_month_weekly_and_daily_keep_their_steps(
    db_session, seed_accounts, seed_customer
):
    """W-85: twice a month (which the API takes) holds to the start day and
    fifteen days before it, where it used to drift to the 1st and the 16th;
    weekly and daily steps take no day of the month and are unchanged."""
    half = _schedule_from(
        db_session, seed_customer.id, date(2026, 1, 31), "semimonthly"
    )
    week = _schedule_from(db_session, seed_customer.id, date(2026, 1, 31), "weekly")
    day = _schedule_from(db_session, seed_customer.id, date(2026, 2, 27), "daily")
    scheduler.catch_up_recurring(db_session, date(2026, 3, 31))
    assert _dates_made(db_session, half.id) == [
        date(2026, 1, 31),
        date(2026, 2, 16),
        date(2026, 2, 28),
        date(2026, 3, 16),
        date(2026, 3, 31),
    ]
    assert _dates_made(db_session, week.id) == [
        date(2026, 1, 31) + timedelta(weeks=n) for n in range(9)
    ]
    assert _dates_made(db_session, day.id) == [
        date(2026, 2, 27) + timedelta(days=n) for n in range(33)
    ]
