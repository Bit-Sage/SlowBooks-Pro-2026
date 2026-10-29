#!/usr/bin/env python3
"""
Standalone cron script to generate due recurring invoices and to enter the
memorized bills and journal entries set to auto-enter (#605); a remind
schedule is left on the Memorized transactions page's due list.
Add to crontab: 0 6 * * * cd /path/to/bookkeeper && python3 scripts/run_recurring.py

The app does this by itself when it opens and once a day while it runs
(app/services/scheduler.py, the auto_enter_on_open setting); this script is
for a server install, and can run beside the app: an occurrence is claimed
in the database before it is posted, so neither enters one twice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import SessionLocal
from app.services.recurring_service import generate_due_invoices
from app.services import memorized_service

if __name__ == "__main__":
    db = SessionLocal()
    try:
        # Collect the skipped templates too: a schedule skipped for a closed
        # period or a $0.00 total keeps its due date and is skipped again on
        # every later run, so the operator's log has to name it and say why.
        skipped: list = []
        ids = generate_due_invoices(db, skipped=skipped)
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
