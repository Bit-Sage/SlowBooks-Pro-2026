"""Re-activating a paused recurring template, and generation that does not
sink the whole batch for one closed installment.

Re-ticking Active resumed from a stale next_due: a template paused in January
and re-activated in July back-dated a pledge/invoice for every missed month,
and if one of those dates was inside the closing date, /api/recurring/generate
answered 403 and made nothing for every schedule. Now Active off -> on rolls
next_due forward to the first occurrence on or after today (missed periods
skipped, not billed), and a single closed installment is skipped and reported
while the rest generate.
"""

import runpy
from datetime import date
from pathlib import Path

import app.database
import app.services.recurring_service as recurring_service
from app.models.recurring import RecurringInvoice
from app.services.recurring_service import roll_next_due_forward

RUN_RECURRING = Path(__file__).resolve().parents[1] / "scripts" / "run_recurring.py"

LINE = {"description": "Monthly service", "quantity": 1, "rate": 100}


def _schedule(client, customer_id, start="2026-01-01"):
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": "monthly",
            "start_date": start,
            "lines": [LINE],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_roll_next_due_forward_skips_whole_periods():
    # Monthly from Jan 1; the first occurrence on or after Jul 15 is Aug 1.
    assert roll_next_due_forward(
        date(2026, 1, 1), "monthly", date(2026, 7, 15)
    ) == date(2026, 8, 1)
    # A next_due already on or after as_of is left alone.
    assert roll_next_due_forward(
        date(2026, 9, 1), "monthly", date(2026, 7, 15)
    ) == date(2026, 9, 1)
    # An occurrence landing exactly on as_of stays.
    assert roll_next_due_forward(date(2026, 1, 1), "monthly", date(2026, 3, 1)) == date(
        2026, 3, 1
    )


def test_reactivating_rolls_next_due_forward_and_bills_no_missed_months(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _schedule(client, seed_customer.id, start="2026-01-01")

    # Pause it (next_due is still the stale Jan 1 start).
    assert (
        client.put(f"/api/recurring/{rec_id}", json={"is_active": False}).status_code
        == 200
    )

    # Re-activate: next_due rolls to the first occurrence on or after today.
    r = client.put(f"/api/recurring/{rec_id}", json={"is_active": True})
    assert r.status_code == 200
    expected = roll_next_due_forward(date(2026, 1, 1), "monthly", date.today())
    assert date.fromisoformat(r.json()["next_due"]) == expected
    assert expected >= date.today()

    # Generating for today back-dates nothing: every invoice made (if any) is
    # dated today or later, never one per missed month.
    made = client.post("/api/recurring/generate").json()
    inv_ids = made["invoice_ids"]
    for inv_id in inv_ids:
        inv = client.get(f"/api/invoices/{inv_id}").json()
        assert date.fromisoformat(inv["date"]) >= date.today()


def test_one_closed_installment_does_not_stop_the_other_schedules(
    client, db_session, seed_accounts, seed_customer
):
    # Books closed through Sep 30.
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )

    closed = _schedule(client, seed_customer.id, start="2026-09-01")  # inside
    healthy = _schedule(client, seed_customer.id, start="2026-10-01")  # after

    out = client.post("/api/recurring/generate?as_of=2026-10-15").json()

    # The healthy schedule still billed; the closed one was skipped, not fatal.
    assert out["invoices_created"] == 1
    skipped_ids = [s["recurring_id"] for s in out["skipped"]]
    assert closed in skipped_ids
    assert healthy not in skipped_ids
    msg = next(s["message"] for s in out["skipped"] if s["recurring_id"] == closed)
    assert "closing date" in msg
    # The advice has to be one the operator can actually follow: RecurringUpdate
    # has no start date or next-due field, so re-ticking Active (which rolls the
    # date forward) is the way out, not "set a later start".
    assert "re-tick Active" in msg
    assert "later start" not in msg

    # The closed template kept its date for a later catch-up and stayed active.
    db_session.expire_all()
    rec = db_session.get(RecurringInvoice, closed)
    assert rec.next_due == date(2026, 9, 1)
    assert rec.is_active is True


def test_run_recurring_script_prints_the_skipped_list(monkeypatch, capsys):
    # The cron script's log is what the operator reads: a template skipped for
    # a closed period keeps its date and is skipped on every later run, so the
    # script has to name each skipped schedule and the reason, not swallow it.
    class _DummySession:
        def close(self):
            pass

    monkeypatch.setattr(app.database, "SessionLocal", lambda: _DummySession())

    def fake_generate(db, as_of=None, skipped=None):
        if skipped is not None:
            skipped.append(
                {
                    "recurring_id": 7,
                    "customer_name": "Acme LLC",
                    "message": (
                        "The schedule for Acme LLC is due 2026-09-01, which is "
                        "on or before the closing date, so nothing was created. "
                        "Move the closing date, or clear and re-tick Active to "
                        "roll the next due date forward past it."
                    ),
                }
            )
        return []

    monkeypatch.setattr(recurring_service, "generate_due_invoices", fake_generate)

    runpy.run_path(str(RUN_RECURRING), run_name="__main__")

    out = capsys.readouterr().out
    assert "No recurring invoices due." in out
    assert "Skipped 1 schedule(s):" in out
    assert "The schedule for Acme LLC is due 2026-09-01" in out
    assert "re-tick Active" in out
