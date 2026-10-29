# ============================================================================
# Recurring Invoice Service — generates invoices from recurring templates
# Feature 2: Infrastructure C (background scheduler / cron)
# ============================================================================

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.recurring import RecurringInvoice
from app.models.invoices import Invoice, InvoiceLine
from app.models.items import Item
from app.services.jobs_service import own_job
from app.services.numbering import next_invoice_number
from app.services.accounting import (
    taxed_copy_lines,
    _q,
    compute_line_totals,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The schedule vocabulary recurring invoices and memorized transactions
# (#605) share: a frequency names the unit, an interval says every how many.
# Monthly with an interval of 3 and quarterly step the same; the word is
# kept so the list says what was chosen.
# ---------------------------------------------------------------------------
FREQUENCIES = ("daily", "weekly", "semimonthly", "monthly", "quarterly", "yearly")
# QuickBooks' names for a unit and a count.
FREQUENCY_ALIASES = {
    "biweekly": ("weekly", 2),
    "every_two_weeks": ("weekly", 2),
    "every_four_weeks": ("weekly", 4),
    "bimonthly": ("monthly", 2),
    "every_two_months": ("monthly", 2),
    "semiannually": ("monthly", 6),
    "twice_a_year": ("monthly", 6),
    "annually": ("yearly", 1),
}
_MONTHS_PER = {"monthly": 1, "quarterly": 3, "yearly": 12}


def normalize_frequency(frequency: str, interval=None) -> tuple[str, int]:
    """The stored (frequency, interval) for what a request said, or
    ValueError naming the problem. "Every two weeks" is weekly with an
    interval of 2; twice a month and quarterly take no interval (every
    N months is monthly with an interval)."""
    word = (frequency or "").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        count = int(interval) if interval is not None else 1
    except (TypeError, ValueError):
        raise ValueError("interval must be a whole number of 1 or more")
    if count < 1:
        raise ValueError("interval must be a whole number of 1 or more")
    if word in FREQUENCY_ALIASES:
        unit, base = FREQUENCY_ALIASES[word]
        return unit, base * count
    if word not in FREQUENCIES:
        raise ValueError(
            "frequency must be one of "
            + ", ".join(FREQUENCIES)
            + " (every N days, weeks or months is daily, weekly or monthly "
            "with an interval)"
        )
    if word in ("semimonthly", "quarterly") and count != 1:
        raise ValueError(
            f"{word} takes no interval; for every N months use monthly with "
            "an interval"
        )
    return word, count


def frequency_label(frequency: str, interval=1) -> str:
    """ "Monthly", "Every two weeks", "Twice a month", "Every 5 days": the
    words the lists show for a schedule."""
    n = int(interval or 1)
    small = {2: "two", 3: "three", 4: "four", 6: "six"}
    if frequency == "semimonthly":
        return "Twice a month"
    if frequency == "quarterly":
        return "Quarterly"
    if frequency == "monthly" and n == 6:
        return "Twice a year"
    unit = {
        "daily": ("Daily", "days"),
        "weekly": ("Weekly", "weeks"),
        "monthly": ("Monthly", "months"),
        "yearly": ("Yearly", "years"),
    }.get(frequency)
    if unit is None:
        return (frequency or "").replace("_", " ").capitalize()
    if n == 1:
        return unit[0]
    return f"Every {small.get(n, n)} {unit[1]}"


def _add_months(current: date, months: int, anchor_day=None) -> date:
    target = current + relativedelta(months=months)
    if anchor_day:
        last = calendar.monthrange(target.year, target.month)[1]
        target = target.replace(day=min(int(anchor_day), last))
    return target


def _next_half_month(current: date, anchor_day=None) -> date:
    """Twice a month: the schedule's day of the month and fifteen days
    after it (the 1st and the 16th; the 15th and the 30th; begun on the
    31st, the 16th and the 31st), each cut back to the last day of a
    shorter month."""
    day = int(anchor_day or current.day)
    first = day - 15 if day > 15 else day
    year, month = current.year, current.month
    for _ in range(2):
        last = calendar.monthrange(year, month)[1]
        for candidate in (first, first + 15):
            d = date(year, month, min(candidate, last))
            if d > current:
                return d
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return current + timedelta(days=15)


def advance_date(
    current: date, frequency: str, interval: int = 1, anchor_day=None
) -> date:
    """The occurrence after `current`. With an anchor day (a memorized
    transaction's), a monthly, quarterly or yearly step holds to that day
    of the month: begun on the 31st it lands on the 28th of February and
    the 31st of March. Without one (a recurring invoice), a step from the
    28th of February stays on the 28th, as it always has."""
    n = max(1, int(interval or 1))
    if frequency == "daily":
        return current + timedelta(days=n)
    if frequency == "weekly":
        return current + timedelta(weeks=n)
    if frequency == "semimonthly":
        return _next_half_month(current, anchor_day)
    return _add_months(current, _MONTHS_PER.get(frequency, 1) * n, anchor_day)


def _advance_next_due(current: date, frequency: str) -> date:
    return advance_date(current, frequency)


def roll_next_due_forward(
    current: date, frequency: str, as_of: date, interval: int = 1, anchor_day=None
) -> date:
    """Advance next_due by whole periods to the first occurrence on or after
    as_of. Missed periods while a schedule was paused are skipped, not billed:
    re-activating a template resumed from a stale next_due and back-dated an
    invoice for every missed period (and a date inside the closing date made
    /api/recurring/generate refuse every schedule). A next_due already on or
    after as_of is left alone."""
    d = current
    guard = 0
    while d < as_of and guard < 10000:
        nxt = advance_date(d, frequency, interval, anchor_day)
        if nxt <= d:
            break
        d = nxt
        guard += 1
    return d


def generate_due_invoices(
    db: Session,
    as_of: date = None,
    skipped: list | None = None,
    only_id: int | None = None,
    dated: date | None = None,
) -> list[int]:
    """Generate all invoices that are due on or before as_of date.
    Returns list of created invoice IDs.

    A template whose lines add up to $0.00 (one saved before the routes
    refused them) generates nothing and keeps its date, so it catches up
    once a rate is entered; pass a list as `skipped` to hear which ones and
    why. Two such $0.00 invoices were generated on the 2.17.3 exploratory
    run and then showed on the dashboard as overdue.

    `only_id` generates that one template's next installment whether or
    not it is due yet (Enter now on the Memorized transactions page, #605),
    dated its scheduled date, or `dated` when given; the schedule advances
    from the scheduled date either way."""
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    words = terms_from_db(db)
    from app.services.settings_service import is_nonprofit

    # A nonprofit's recurring invoices are pledges (printed as such)
    nonprofit = is_nonprofit(db)
    if as_of is None:
        as_of = date.today()

    if only_id is not None:
        recurrings = (
            db.query(RecurringInvoice).filter(RecurringInvoice.id == only_id).all()
        )
    else:
        recurrings = (
            db.query(RecurringInvoice)
            .filter(
                RecurringInvoice.is_active,
                RecurringInvoice.next_due <= as_of,
            )
            .all()
        )

    created_ids = []
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    for rec in recurrings:
        # Check end date
        if rec.end_date and rec.next_due > rec.end_date:
            rec.is_active = False
            continue
        # The invoice's date: the scheduled one, or the day Enter now asked for
        on = dated if (dated is not None and only_id is not None) else rec.next_due

        # Compute totals via the shared helper: each line is rounded to 2dp
        # before summing so the stored subtotal matches the sum of stored
        # line amounts, and journal credits land on the same cents as the
        # A/R debit once SQL rounds.
        tax_rate = rec.tax_rate or Decimal("0")
        # the customer's CURRENT tax treatment, not the template's saved flags
        copied = taxed_copy_lines(rec.lines, rec.customer)
        subtotal, tax_amount, total = compute_line_totals(copied, tax_rate)
        if total <= 0:
            if skipped is not None:
                who = rec.customer.name if rec.customer else f"schedule {rec.id}"
                skipped.append(
                    {
                        "recurring_id": rec.id,
                        "customer_name": rec.customer.name if rec.customer else None,
                        "message": (
                            f"The schedule for {who} adds up to $0.00, so "
                            "nothing was created. Open it and enter a rate "
                            "on at least one line."
                        ),
                    }
                )
            continue

        # One template whose installment lands in a closed period must not
        # sink the whole batch: check_closing_date raised a 403 that aborted
        # generation for every schedule (a healthy one made 0, and the daily
        # run failed the same way). Skip and report this one, keep the rest.
        # A valid override password on the request still passes through.
        from app.services.closing_date import check_closing_date

        try:
            check_closing_date(db, on)
        except HTTPException as e:
            if e.status_code != 403:
                raise
            if skipped is not None:
                who = rec.customer.name if rec.customer else f"schedule {rec.id}"
                skipped.append(
                    {
                        "recurring_id": rec.id,
                        "customer_name": rec.customer.name if rec.customer else None,
                        "message": (
                            f"The schedule for {who} is due {on}, which "
                            "is on or before the closing date, so nothing was "
                            "created. Move the closing date, or clear and re-tick "
                            "Active to roll the next due date forward past it."
                        ),
                    }
                )
            continue

        # Parse terms for due date
        due_date = on + timedelta(days=30)
        if rec.terms:
            try:
                days = int(rec.terms.lower().replace("net ", ""))
                due_date = on + timedelta(days=days)
            except ValueError:
                pass

        # MAX+1 numbering races against concurrent manual creates (same as
        # the invoices route). Retry under a SAVEPOINT so a collision rolls
        # back only this attempt — not the other invoices already generated
        # in this batch — and a template that can't get a number is simply
        # left for the next run instead of aborting the whole batch.
        # The invoice carries the schedule's customer's job only: another
        # customer's job stored on the schedule (books from before the
        # rule) is left off rather than minted again every period, and the
        # invoice is still generated (NEW-36).
        left_off: list[str] = []
        job_id = own_job(db, rec.customer_id, rec.job_id, left_off)
        if left_off:
            logger.warning(
                "Recurring invoice %s for %s: job %s belongs to a different "
                "customer and was left off the generated invoice",
                rec.id,
                rec.customer.name if rec.customer else rec.customer_id,
                ", ".join(left_off),
            )

        invoice = None
        invoice_number = None
        for _ in range(10):
            invoice_number = next_invoice_number(db)
            candidate = Invoice(
                invoice_number=invoice_number,
                customer_id=rec.customer_id,
                date=on,
                due_date=due_date,
                terms=rec.terms,
                subtotal=subtotal,
                tax_rate=tax_rate,
                tax_amount=tax_amount,
                total=total,
                balance_due=total,
                notes=rec.notes,
                class_id=rec.class_id,
                job_id=job_id,
                recurring_invoice_id=rec.id,
                is_pledge=nonprofit,
            )
            nested = db.begin_nested()
            db.add(candidate)
            try:
                db.flush()
                nested.commit()
                invoice = candidate
                break
            except IntegrityError as e:
                nested.rollback()
                if "invoice_number" not in str(e.orig).lower():
                    raise
        if invoice is None:
            continue

        for rline, cline in zip(rec.lines, copied):
            db.add(
                InvoiceLine(
                    invoice_id=invoice.id,
                    item_id=rline.item_id,
                    description=rline.description,
                    quantity=rline.quantity,
                    rate=rline.rate,
                    is_taxable=cline.is_taxable,
                    amount=_q(Decimal(str(rline.quantity)) * Decimal(str(rline.rate))),
                    line_order=rline.line_order,
                )
            )

        # Journal entry
        if ar_id and default_income_id:
            journal_lines = [
                {
                    "account_id": ar_id,
                    "debit": total,
                    "credit": Decimal("0"),
                    "description": f"Recurring {document_label(candidate, words)} #{invoice_number}",
                }
            ]
            for rline in rec.lines:
                # Must round per-line BEFORE summing to match compute_line_totals;
                # otherwise stored credits drift from the rounded A/R debit.
                line_amt = _q(Decimal(str(rline.quantity)) * Decimal(str(rline.rate)))
                if line_amt == 0:
                    continue
                income_id = default_income_id
                if rline.item_id:
                    item = db.query(Item).filter(Item.id == rline.item_id).first()
                    if item and item.income_account_id:
                        income_id = item.income_account_id
                journal_lines.append(
                    {
                        "account_id": income_id,
                        "debit": Decimal("0"),
                        "credit": line_amt,
                        "description": rline.description or "",
                    }
                )
            if tax_amount > 0 and tax_account_id:
                journal_lines.append(
                    {
                        "account_id": tax_account_id,
                        "debit": Decimal("0"),
                        "credit": tax_amount,
                        "description": "Sales tax",
                    }
                )
            txn = create_journal_entry(
                db,
                on,
                f"Recurring {document_label(candidate, words)} #{invoice_number}",
                journal_lines,
                source_type="invoice",
                source_id=invoice.id,
                reference=invoice_number,
                class_id=invoice.class_id,
                job_id=invoice.job_id,
            )
            invoice.transaction_id = txn.id

        # Phase 11 (audit fix): recurring-generated invoices are real sales
        # and must hit the inventory ledger.
        db.flush()
        db.refresh(invoice)
        from app.services.inventory_hooks import post_sale_for_invoice

        post_sale_for_invoice(db, invoice, txn_date=on)

        # Advance next due date
        rec.next_due = _advance_next_due(rec.next_due, rec.frequency)
        rec.invoices_created += 1
        if rec.end_date and rec.next_due > rec.end_date:
            rec.is_active = False

        created_ids.append(invoice.id)

    db.commit()
    return created_ids
