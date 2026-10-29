# ============================================================================
# The in-app scheduler (#605, 2.31.1 gate W-66): the app enters what is due
# when it opens and once a day while it runs, so an auto-enter memorized
# bill or journal entry is entered on its date without anyone pressing Enter
# All Due, every missed occurrence in order up to today, and keeps a note of
# what it entered for the Memorized transactions page to show ("Entered 3
# scheduled documents while you were away"). Recurring invoices are not
# entered by the automatic runs: they have always waited for the Generate
# button (or the server's cron), and a company upgrading with a schedule
# overdue must not find invoices posted, and owed, that nobody asked for
# (2.31.1 gate, Linux round 2: the Riverbend comparison). Enter All Due,
# pressed by a person, runs both. The setting auto_enter_on_open turns the
# automatic runs off; the cron script (scripts/run_recurring.py) stays for a
# server install.
#
# Nothing is entered before a person could have signed in (2.31.1 gate
# W-70): when the company has a password, so the app shows its sign-in
# screen, the startup run waits for the first sign-in to it since this
# process started (a password typed, or a sign-in the app still remembers
# reaching the server), then runs; the daily run, likewise, only once
# someone has. A company with no password yet runs at open, as before. The
# documents a run enters carry, in the audit log, the username of the person
# signed in (the latest sign-in), or "scheduled run" when nobody signs in.
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
# cron run beside the app) and against Enter now in another window, the
# claims keep anything from being entered twice: a memorized occurrence is
# claimed by memorized_service.claim_occurrence and a recurring invoice's
# installment by recurring_service.claim_installment, each in one guarded
# UPDATE before anything posts.
_RUN_LOCK = threading.RLock()
_last_run: dict | None = None

# A recurring invoice schedule is generated one installment per pass; a run
# makes passes until a pass generates nothing (2.31.1 gate W-81: a fixed
# 1,000 passes left a longer backlog for a second run). The cap is a safety
# net no real backlog meets (a daily schedule some 270 years behind);
# reached, it is logged and what is still due waits for the next run.
PASS_SAFETY_CAP = 100_000
# How often the task looks at the calendar. A change of day runs the
# schedules, so a machine asleep over midnight catches up on waking.
POLL_SECONDS = 15 * 60
# SLOWBOOKS_AUTO_ENTER=0 keeps the task from starting at all: a server
# install that runs the cron script instead, and the test suite.
ENV_SWITCH = "SLOWBOOKS_AUTO_ENTER"
# Who the audit log names for what a run enters when nobody signs in (a
# company with no password, the cron script).
SCHEDULED_RUN = "scheduled run"
# What run_in_this_process answers while the company needs a sign-in and
# nobody has signed in since the process started: the task tries again.
WAITING_FOR_SIGN_IN = "waiting for a sign-in"

# The latest person signed in to the company this server was started for,
# since the process started (note_sign_in); None until somebody has.
_signed_in_as: str | None = None
# The task's wake-up: a sign-in sets it, so the waiting run goes at once.
_wake: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def note_sign_in(username: str | None) -> None:
    """A person is signed in to the company this server was started for:
    called by the sign-in, first-run setup and company switch routes, and
    by the gate for each request of a signed-in session it lets through
    (a sign-in the app remembers, ask_password_on_start off). The first one
    wakes the task, whose startup run was waiting for it."""
    global _signed_in_as
    first = _signed_in_as is None
    _signed_in_as = username or "operator"
    if first:
        _wake_the_task()


def _wake_the_task() -> None:
    loop, event = _wake_loop, _wake
    if loop is None or event is None:
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:  # the task's loop has closed
        pass


def needs_sign_in(db: Session) -> bool:
    """The company shows a sign-in screen before anything can be done in
    it: it has a password (set at first-run setup). Without one, nobody
    signs in, and the app runs the schedules at open, as it always has."""
    from app.services.auth import password_is_set

    return password_is_set(db)


def catch_up_recurring(
    db: Session, as_of: date | None = None
) -> tuple[list[int], list[dict]]:
    """Every due recurring invoice, every missed installment up to as_of
    (today), each dated its scheduled date: one installment per schedule
    per pass, passes until one generates nothing. What Enter All Due does
    and what the cron script does, so the two agree (W-81). Returns the
    invoices made and the schedules skipped, each named once."""
    if as_of is None:
        as_of = date.today()
    invoice_ids: list[int] = []
    skipped: list[dict] = []
    seen: set = set()
    passes = 0
    while True:
        this_pass: list[dict] = []
        ids = recurring_service.generate_due_invoices(db, as_of, skipped=this_pass)
        # a schedule skipped for a closed period keeps its date and is
        # skipped on every pass: reported once
        for item in this_pass:
            key = item.get("recurring_id")
            if key not in seen:
                seen.add(key)
                skipped.append(item)
        if not ids:
            break
        invoice_ids.extend(ids)
        passes += 1
        if passes >= PASS_SAFETY_CAP:
            logger.warning(
                "the recurring invoice catch-up stopped after %d passes (%d "
                "invoices); what is still due is generated on the next run",
                passes,
                len(invoice_ids),
            )
            break
    return invoice_ids, skipped


def run_due_schedules(
    db: Session, as_of: date | None = None, include_recurring: bool = True
) -> dict:
    """What one run does, on the session given, under the lock: every due
    auto-enter memorized bill and journal entry (every missed occurrence, in
    date order) and, when include_recurring (Enter All Due, pressed by a
    person), every due recurring invoice (every missed installment,
    catch_up_recurring), up to as_of (today). The automatic runs pass
    include_recurring False. Returns what it entered and what it had to
    skip."""
    with _RUN_LOCK:
        if as_of is None:
            as_of = date.today()
        invoice_ids: list[int] = []
        invoice_skipped: list[dict] = []
        if include_recurring:
            invoice_ids, invoice_skipped = catch_up_recurring(db, as_of)
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


def run_in_this_process(origin: str = "startup", today=date.today):
    """One run for the company this server was started for, on a session of
    its own. Nothing when the setting is off, or when the run fails (logged,
    never raised: the task has to outlive a bad day); WAITING_FOR_SIGN_IN,
    entering nothing, while the company has a password and nobody has
    signed in since the process started (W-70). What it did is kept for the
    page (last_run)."""
    global _last_run

    # looked up at call time: the test harness and the desktop launcher
    # both repoint app.database.SessionLocal
    import app.database as database

    try:
        db = database.SessionLocal()
        try:
            if not enabled(db):
                return None
            signed_in = _signed_in_as
            if signed_in is None and needs_sign_in(db):
                return WAITING_FOR_SIGN_IN
            # the audit log names who was signed in for what this run enters
            db.info["acting_username"] = signed_in or SCHEDULED_RUN
            # memorized auto-enter schedules only (see the module note)
            result = run_due_schedules(db, today(), include_recurring=False)
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


async def _nap(seconds: float) -> None:
    """poll_seconds, or less: the first sign-in wakes the task at once."""
    event = _wake
    if event is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(event.wait(), seconds)
    except asyncio.TimeoutError:
        pass
    event.clear()


async def keep_entering(
    today=date.today,
    sleep=_nap,
    to_thread=asyncio.to_thread,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    """The task: a run at startup, then one each time the calendar day
    changes, checked every poll_seconds (so a laptop asleep over midnight
    runs on waking). A run that has to wait for a sign-in is tried again
    when the first sign-in wakes the task (or at the next look), still as
    the startup run. The run itself is blocking database work and goes to
    a thread. Cancelled by the lifespan on shutdown."""
    global _wake, _wake_loop
    _wake, _wake_loop = asyncio.Event(), asyncio.get_running_loop()
    last_day = None
    origin = "startup"
    told = False
    while True:
        day = today()
        if day != last_day:
            ran = await to_thread(run_in_this_process, origin, today)
            if ran == WAITING_FOR_SIGN_IN:
                if not told:
                    logger.info(
                        "the %s run of the schedules waits for someone to sign in",
                        origin,
                    )
                    told = True
            else:
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
