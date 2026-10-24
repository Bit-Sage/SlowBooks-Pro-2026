#!/usr/bin/env python3
"""
Standalone cron script to generate due recurring invoices.
Add to crontab: 0 6 * * * cd /path/to/bookkeeper && python3 scripts/run_recurring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.services.recurring_service import generate_due_invoices

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
    finally:
        db.close()
