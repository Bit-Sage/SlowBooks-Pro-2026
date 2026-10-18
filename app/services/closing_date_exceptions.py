"""Closing Date Exceptions (#342): what moved inside a closed period.

Posting into a closed period takes the closing-date password. Each use
writes an OVERRIDE audit row (closing_date._record_override: the closing
date in force and the transaction's date), and from 2.24.0 every row that
request changes after it carries the audit source "closing_date_override"
(audit._after_flush). This report reads the two together: one event per
override, with the rows it touched (which record, what action, each field
before and after), for the period in which the changes were made. The
report QuickBooks accountants check at every review, by the same name.

An override recorded before 2.24.0 shows its dates and who made it; the
rows it changed carried the ordinary source then, so they are not listed.
"""

from __future__ import annotations

from datetime import date
import json

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.services.closing_date import get_closing_date

OVERRIDE_SOURCE = "closing_date_override"

# What a table's rows are called on the report
_RECORD_WORDS = {
    "transactions": "journal entry",
    "transaction_lines": "journal line",
    "invoices": "invoice",
    "invoice_lines": "invoice line",
    "bills": "bill",
    "bill_lines": "bill line",
    "bill_payments": "bill payment",
    "payments": "payment",
    "payment_allocations": "payment allocation",
    "credit_memos": "credit memo",
    "vendor_credits": "vendor credit",
    "deposits": "deposit",
    "sales_receipts": "sales receipt",
    "estimates": "estimate",
    "cc_charges": "card charge",
    "expenses": "expense",
    "accounts": "account",
    "settings": "setting",
}
# The fields that name a record, in the order tried
_NAME_FIELDS = (
    "invoice_number",
    "bill_number",
    "receipt_number",
    "estimate_number",
    "check_number",
    "number",
    "reference",
    "description",
    "memo",
    "name",
    "key",
)
# Bookkeeping fields nobody needs to see change
_QUIET_FIELDS = {"updated_at", "created_at", "balance"}


def record_word(table_name: str) -> str:
    if table_name in _RECORD_WORDS:
        return _RECORD_WORDS[table_name]
    word = table_name.replace("_", " ")
    return word[:-1] if word.endswith("s") else word


def _label(values: dict | None) -> str:
    values = values or {}
    for key in _NAME_FIELDS:
        v = values.get(key)
        if v not in (None, "", "***"):
            return str(v)
    return ""


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _fields(row: AuditLog) -> list[dict]:
    """Each field a change moved, before and after. An INSERT lists the
    new row's named fields; a DELETE the old row's."""
    if row.action == "UPDATE":
        names = [f for f in (row.changed_fields or []) if f not in _QUIET_FIELDS]
        old, new = row.old_values or {}, row.new_values or {}
        return [
            {"field": f, "before": _text(old.get(f)), "after": _text(new.get(f))}
            for f in names
        ]
    values = row.new_values if row.action == "INSERT" else (row.old_values or {})
    values = values or {}
    shown = [
        f
        for f in (
            "date",
            *_NAME_FIELDS,
            "total",
            "amount",
            "total_amount",
            "debit",
            "credit",
            "account_id",
            "status",
        )
        if f in values and values.get(f) not in (None, "", "***")
    ]
    if row.action == "INSERT":
        return [{"field": f, "before": "", "after": _text(values[f])} for f in shown]
    return [{"field": f, "before": _text(values[f]), "after": ""} for f in shown]


# for the package's audit-log CSV (#343)
fields_of = _fields
label_of = _label


def _change(row: AuditLog) -> dict:
    values = row.new_values if row.action != "DELETE" else row.old_values
    return {
        "audit_id": row.id,
        "when": row.timestamp.isoformat() if row.timestamp else "",
        "who": row.username or "",
        "table": row.table_name,
        "record": record_word(row.table_name),
        "record_id": row.record_id,
        "label": _label(values),
        "action": row.action.lower(),
        "fields": _fields(row),
    }


def closing_date_exceptions(db: Session, start_date: date, end_date: date) -> dict:
    """The overrides made in the period (by the day they were made), each
    with the rows it changed."""
    overrides = (
        db.query(AuditLog)
        .filter(
            AuditLog.table_name == "closing_date",
            AuditLog.action == "OVERRIDE",
            sqlfunc.date(AuditLog.timestamp) >= start_date,
            sqlfunc.date(AuditLog.timestamp) <= end_date,
        )
        .order_by(AuditLog.id)
        .all()
    )
    events = []
    for ov in overrides:
        values = ov.new_values or {}
        events.append(
            {
                "audit_id": ov.id,
                "when": ov.timestamp.isoformat() if ov.timestamp else "",
                "who": ov.username or "",
                "closing_date": values.get("closing_date") or "",
                "transaction_date": values.get("transaction_date") or "",
                "changes": [],
            }
        )
    if overrides:
        changed = (
            db.query(AuditLog)
            .filter(
                AuditLog.source == OVERRIDE_SOURCE,
                AuditLog.table_name != "closing_date",
                AuditLog.id > overrides[0].id,
            )
            .order_by(AuditLog.id)
            .all()
        )
        # A change belongs to the latest override before it by the same
        # sign-in: an override's request writes its own row first, and
        # another person's request writes under their own name.
        by_user: dict[str, list[dict]] = {}
        for ev in events:
            by_user.setdefault(ev["who"], []).append(ev)
        for row in changed:
            mine = by_user.get(row.username or "")
            if not mine:
                continue
            owner = None
            for ev in mine:
                if ev["audit_id"] < row.id:
                    owner = ev
                else:
                    break
            if owner is not None:
                change = _change(row)
                # an account's balance moving with the posting is not a
                # change anyone made: nothing to show, so nothing listed
                if change["action"] == "update" and not change["fields"]:
                    continue
                owner["changes"].append(change)
    closing = get_closing_date(db)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "closing_date": closing.isoformat() if closing else "",
        "events": events,
        "count": len(events),
        "changes": sum(len(ev["changes"]) for ev in events),
    }


def exceptions_csv(data: dict, company: str) -> str:
    """One row per field a change moved (a change with no field listed
    gets one row of its own), the event's words on every row."""
    import io

    from app.services.csv_export import _SafeWriter

    out = io.StringIO()
    w = _SafeWriter(out)
    w.writerow(["Company", company])
    w.writerow(["Report", "Closing Date Exceptions"])
    w.writerow(["Period", f"{data['start_date']} to {data['end_date']}"])
    w.writerow([])
    w.writerow(
        [
            "When",
            "Who",
            "Closing date",
            "Transaction date",
            "Record",
            "Action",
            "Field",
            "Before",
            "After",
        ]
    )
    for ev in data["events"]:
        head = [ev["when"], ev["who"], ev["closing_date"], ev["transaction_date"]]
        if not ev["changes"]:
            w.writerow(head + ["", "override used", "", "", ""])
        for ch in ev["changes"]:
            record = f"{ch['record']} {ch['record_id']}"
            if ch["label"]:
                record += f" ({ch['label']})"
            if not ch["fields"]:
                w.writerow(head + [record, ch["action"], "", "", ""])
            for f in ch["fields"]:
                w.writerow(
                    head + [record, ch["action"], f["field"], f["before"], f["after"]]
                )
    w.writerow([])
    w.writerow(["Overrides", data["count"], "Changes", data["changes"]])
    return out.getvalue()


def exceptions_section(data: dict) -> dict:
    """The report as a PDF section."""
    rows = []
    for ev in data["events"]:
        head = (
            f"{ev['when'][:16].replace('T', ' ')}  {ev['who']}  "
            f"closing date {ev['closing_date']}, a {ev['transaction_date']} change"
        )
        rows.append({"cells": [head, "", "", ""], "style": "subtotal"})
        if not ev["changes"]:
            rows.append(
                {
                    "cells": [
                        "    (the override was used; the rows it changed were not marked before 2.24.0)",
                        "",
                        "",
                        "",
                    ]
                }
            )
        for ch in ev["changes"]:
            record = f"    {ch['action']} {ch['record']} {ch['record_id']}"
            if ch["label"]:
                record += f" ({ch['label']})"
            if not ch["fields"]:
                rows.append({"cells": [record, "", "", ""]})
            for i, f in enumerate(ch["fields"]):
                rows.append(
                    {
                        "cells": [
                            record if i == 0 else "",
                            f["field"],
                            f["before"],
                            f["after"],
                        ]
                    }
                )
    rows.append(
        {
            "cells": [
                f"{data['count']} overrides, {data['changes']} changes",
                "",
                "",
                "",
            ],
            "style": "grand-total",
        }
    )
    period = f"{data['start_date']} — {data['end_date']}"
    if data.get("closing_date"):
        period += f" · closing date now {data['closing_date']}"
    return {
        "title": "Closing Date Exceptions",
        "period": period,
        "columns": ["Change", "Field", "Before", "After"],
        "rows": rows,
    }
