# ============================================================================
# The in-app scheduler (#605, 2.31.1 gate W-66): the app enters what is due
# when it opens and once a day while it runs, so an auto-enter schedule is
# entered on its date without anyone pressing Enter All Due. One run
# generates every due recurring invoice and enters every due auto-enter
# memorized bill and journal entry, every missed occurrence in order up to
# today, and keeps a note of what it entered for the Memorized transactions
# page to show ("Entered 3 scheduled documents while you were away"). The
# setting auto_enter_on_open turns the automatic runs off; the cron script
# (scripts/run_recurring.py) stays for a server install.
# ============================================================================

import asyncio
import logging
import os
import threading
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.services import memorized_service, recurring_service

logger = logging.getLogger(__name__)

# The runs of one process take turns: the startup run, the daily run and
# Enter All Due never interleave. Across processes (two uvicorn workers, a
# cron run beside the app) the claim in memorized_service.claim_occurrence
# keeps an occurrence from being entered twice.
_RUN_LOCK = threading.RLock()
_last_run: dict | None = None

# A recurring invoice schedule is generated one installment per pass; a run
# makes passes until nothing is due, this many at most.
PASS_LIMIT = 1000
# How often the task looks at the calendar. A change of day runs the
# schedules, so a machine asleep over midnight catches up on waking.
POLL_SECONDS = 15 * 60
# SLOWBOOKS_AUTO_ENTER=0 keeps the task from starting at all: a server
# install that runs the cron script instead, and the test suite.
ENV_SWITCH = "SLOWBOOKS_AUTO_ENTER"


def run_due_schedules(db: Session, as_of: date | None = None) -> dict:
    """What one run does, on the session given, under the lock: every due
    recurring invoice (every missed installment, a pass each) and every due
    auto-enter memorized bill and journal entry (every missed occurrence, in
    date order), up to as_of (today). Enter All Due on the page is this
    too. Returns what it entered and what it had to skip."""
    with _RUN_LOCK:
        if as_of is None:
            as_of = date.today()
        invoice_ids: list[int] = []
        invoice_skipped: list[dict] = []
        seen: set = set()
        for _ in range(PASS_LIMIT):
            this_pass: list[dict] = []
            ids = recurring_service.generate_due_invoices(db, as_of, skipped=this_pass)
            # a schedule skipped for a closed period keeps its date and is
            # skipped on every pass: reported once
            for item in this_pass:
                key = item.get("recurring_id")
                if key not in seen:
                    seen.add(key)
                    invoice_skipped.append(item)
            if not ids:
                break
            invoice_ids.extend(ids)
        held: list[dict] = []
        entered = memorized_service.enter_due_memorized(db, as_of, skipped=held)
        return {
            "as_of": as_of.isoformat(),
            "invoices_created": len(invoice_ids),
            "invoice_ids": invoice_ids,
            "entered": entered,
            "entered_count": len(entered),
            "skipped": invoice_skipped + held,
        }


def enabled(db: Session) -> bool:
    from app.services.settings_service import auto_enter_on_open

    return auto_enter_on_open(db)


def run_in_this_process(origin: str = "startup", today=date.today) -> dict | None:
    """One run for the company this server was started for, on a session of
    its own. Nothing when the setting is off, or when the run fails (logged,
    never raised: the task has to outlive a bad day). What it did is kept
    for the page (last_run)."""
    global _last_run

    # looked up at call time: the test harness and the desktop launcher
    # both repoint app.database.SessionLocal
    import app.database as database

    try:
        db = database.SessionLocal()
        try:
            if not enabled(db):
                return None
            result = run_due_schedules(db, today())
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.exception("the %s run of the schedules failed", origin)
        return None
    result["origin"] = origin
    result["ran_at"] = datetime.now().isoformat(timespec="seconds")
    _last_run = result
    if result["invoices_created"] or result["entered_count"] or result["skipped"]:
        logger.info(
            "the %s run entered %d recurring invoice(s) and %d memorized "
            "transaction(s), %d skipped",
            origin,
            result["invoices_created"],
            result["entered_count"],
            len(result["skipped"]),
        )
    return result


def last_run() -> dict | None:
    """What the last run in this process did, for the page: None before
    the first."""
    return _last_run


async def keep_entering(
    today=date.today,
    sleep=asyncio.sleep,
    to_thread=asyncio.to_thread,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    """The task: a run at startup, then one each time the calendar day
    changes, checked every poll_seconds (so a laptop asleep over midnight
    runs on waking). The run itself is blocking database work and goes to
    a thread. Cancelled by the lifespan on shutdown."""
    last_day = None
    origin = "startup"
    while True:
        day = today()
        if day != last_day:
            await to_thread(run_in_this_process, origin, today)
            last_day = day
            origin = "daily"
        await sleep(poll_seconds)


def start():
    """Started by the app's lifespan: the task, or None when
    SLOWBOOKS_AUTO_ENTER=0."""
    if os.environ.get(ENV_SWITCH, "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    return asyncio.get_running_loop().create_task(
        keep_entering(), name="slowbooks-auto-enter"
    )


async def stop(task) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
