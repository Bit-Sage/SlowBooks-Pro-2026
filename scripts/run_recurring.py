#!/usr/bin/env python3
"""
Standalone cron script to generate due recurring invoices and to enter the
memorized bills and journal entries set to auto-enter (#605); a remind
schedule is left on the Memorized transactions page's due list.
Add to crontab: 0 6 * * * cd /path/to/bookkeeper && python3 scripts/run_recurring.py

It catches up both kinds the way Enter All Due does (the same two steps,
app/services/scheduler.py catch_up_recurring and memorized_service
enter_due_memorized): every missed installment of a recurring invoice and
every missed occurrence of an auto-enter memorized bill or journal entry up
to today, each dated its scheduled date, so a server's cron and Enter All
Due agree. Before 2.31.1 the script generated one installment of each
recurring invoice per run.

The app enters due memorized bills and journal entries by itself when it
opens and once a day while it runs (the auto_enter_on_open setting); this
script is for a server install, and can run beside the app: a memorized
occurrence and a recurring invoice's installment are each claimed in the
database before anything is posted, so neither enters one twice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import SessionLocal
import app.models  # noqa: F401,E402  (every table, so an invoice's foreign keys resolve)
from app.services import memorized_service, scheduler

if __name__ == "__main__":
    db = SessionLocal()
    # nobody signs in to a cron run: the audit log names it
    db.info["acting_username"] = scheduler.SCHEDULED_RUN
    try:
        # Collect the skipped templates too: a schedule skipped for a closed
        # period or a $0.00 total keeps its due date and is skipped again on
        # every later run, so the operator's log has to name it and say why.
        ids, skipped = scheduler.catch_up_recurring(db)
        if ids:
            print(f"Generated {len(ids)} invoice(s): {ids}")
        else:
            print("No recurring invoices due.")
        if skipped:
            print(f"Skipped {len(skipped)} schedule(s):")
            for item in skipped:
                print(f"  - {item['message']}")

        held: list = []
        try:
            entered = memorized_service.enter_due_memorized(db, skipped=held)
        except (OperationalError, ProgrammingError) as e:
            # a self-managed database that has not run the 2.31.1 migration:
            # the invoices above are posted; say what is missing and stop
            db.rollback()
            print(
                "Memorized transactions were not entered: the database needs "
                f"`alembic upgrade head` first ({e.__class__.__name__}).",
                file=sys.stderr,
            )
            sys.exit(1)
        if entered:
            print(f"Entered {len(entered)} memorized transaction(s):")
            for item in entered:
                print(
                    f"  - {item['name']} ({item['kind']} dated {item['date']}, "
                    f"next {item['next_date']})"
                )
        else:
            print("No memorized transactions due to auto-enter.")
        if held:
            print(f"Skipped {len(held)} memorized transaction(s):")
            for item in held:
                print(f"  - {item['message']}")
    finally:
        db.close()
