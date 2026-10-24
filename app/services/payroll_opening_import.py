# ============================================================================
# Opening YTD import (#433) — every employee's opening balance from one CSV.
#
# One reader: the SlowBooks template (opening_template_csv). A reader for
# QuickBooks Desktop's Payroll Summary waits for a real export to build
# against; a named third-party format is never read from a hand-made file.
#
# Dry run first, as the chart import: the plan is computed and nothing is
# written; the apply posts the same file and writes exactly that plan.
# ============================================================================

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.benefits import BenefitCode
from app.models.payroll import Employee
from app.models.payroll_opening import OPENING_AMOUNT_FIELDS
from app.services import payroll_opening as po

# Template columns, in order, and the field each fills.
TEMPLATE_AMOUNT_COLUMNS = (
    ("Gross", "gross"),
    ("Federal withholding", "federal_tax"),
    ("SS wages", "ss_wages"),
    ("SS tax", "ss_tax"),
    ("Employer SS tax", "employer_ss_tax"),
    ("Medicare wages", "medicare_wages"),
    ("Medicare tax", "medicare_tax"),
    ("Employer Medicare tax", "employer_medicare_tax"),
    ("FUTA wages", "futa_wages"),
    ("FUTA tax", "futa_tax"),
    ("SUTA wages", "suta_wages"),
    ("SUTA tax", "suta_tax"),
    ("Pre-tax deductions", "pretax_deductions"),
    ("Post-tax deductions", "posttax_deductions"),
    ("Garnishments", "garnishments"),
    ("Net", "net"),
)
TEMPLATE_STATE_COLUMNS = (
    ("State", "state"),
    ("State wages", "wages"),
    ("State withholding", "withholding"),
)
TEMPLATE_COLUMNS = (
    ["Employee", "Email", "Year", "As of"]
    + [c for c, _ in TEMPLATE_AMOUNT_COLUMNS]
    + [c for c, _ in TEMPLATE_STATE_COLUMNS]
)

_BENEFIT_EMPLOYER_RE = re.compile(r"^benefit employer:\s*(.+)$", re.I)
_BENEFIT_RE = re.compile(r"^benefit:\s*(.+)$", re.I)


def opening_template_csv() -> str:
    """The template: the columns, and an example of a two-state employee
    with a 401(k) code (the example's name matches no one, so importing
    the file unchanged writes nothing)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(TEMPLATE_COLUMNS + ["Benefit:401K", "Benefit employer:401K"])
    example = {
        "Employee": "Example, Pat",
        "Email": "",
        "Year": "2026",
        "As of": "2026-06-30",
        "Gross": "30000.00",
        "Federal withholding": "3100.00",
        "SS wages": "29000.00",
        "SS tax": "1798.00",
        "Employer SS tax": "1798.00",
        "Medicare wages": "29000.00",
        "Medicare tax": "420.50",
        "Employer Medicare tax": "420.50",
        "FUTA wages": "7000.00",
        "FUTA tax": "42.00",
        "SUTA wages": "7000.00",
        "SUTA tax": "189.00",
        "Pre-tax deductions": "1500.00",
        "Post-tax deductions": "0.00",
        "Garnishments": "0.00",
        "Net": "21772.50",
        "State": "GA",
        "State wages": "20000.00",
        "State withholding": "800.00",
    }
    w.writerow([example.get(c, "") for c in TEMPLATE_COLUMNS] + ["1500.00", "750.00"])
    second = {"Employee": "Example, Pat", "State": "AL"}
    second.update({"State wages": "8500.00", "State withholding": "340.00"})
    w.writerow([second.get(c, "") for c in TEMPLATE_COLUMNS] + ["", ""])
    return out.getvalue()


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _person_key(name: str) -> str:
    """'Ortiz, Sam' and 'Sam Ortiz' are the same person."""
    name = _norm(name)
    if "," in name:
        last, first = (p.strip() for p in name.split(",", 1))
        name = f"{first} {last}".strip()
    return name


def _match_employee(db: Session, name: str, email: str | None):
    """(employee, error). Name as on the employee, either order, any case;
    the email decides between two of the same name."""
    key = _person_key(name)
    if not key:
        return None, "No employee name."
    found = [
        e
        for e in db.query(Employee).all()
        if _person_key(f"{e.first_name} {e.last_name}") == key
    ]
    if len(found) > 1 and email:
        found = [e for e in found if _norm(e.email or "") == _norm(email)]
    if not found:
        return None, f"No employee named {name.strip()!r}."
    if len(found) > 1:
        return None, (
            f"{len(found)} employees are named {name.strip()!r}; add their "
            "email in an Email column to tell them apart."
        )
    return found[0], None


def _num(cell):
    """An amount as the file writes it ('1,234.56', '$12', '(5.00)',
    '-5.00'), as a string clean_payload can read; None when blank."""
    s = (cell or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return s


def _parse_date(s):
    s = (s or "").strip()
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _plan_row(db, line, name, employee, payload, errors, notes, reason):
    """One plan row: what would be written for the employee, and whether
    it is a create, a replace or an error (with the reason)."""
    clean, problems = po.clean_payload(db, payload)
    errors = list(errors) + problems
    action = "error"
    if not errors and employee is not None:
        existing = po.opening_for(db, employee.id, clean["year"])
        first = po.first_processed_pay_date(db, employee.id, clean["year"])
        if first is not None and existing is None and clean["as_of"] >= first:
            errors.append(
                f"as_of {clean['as_of'].isoformat()} is not before the first "
                f"processed pay run of {clean['year']} ({first.isoformat()})."
            )
        elif first is not None and not reason:
            errors.append(po.lock_message(first) + " Import again with a reason.")
        else:
            action = "replace" if existing else "create"
    row = {
        "line": line,
        "employee": name,
        "employee_id": employee.id if employee else None,
        "year": clean.get("year"),
        "as_of": (
            clean["as_of"].isoformat()
            if isinstance(clean.get("as_of"), date)
            else clean.get("as_of")
        ),
        "action": action,
        "note": "; ".join(errors + notes) or None,
        "errors": errors,
    }
    for f in OPENING_AMOUNT_FIELDS:
        row[f] = float(clean[f])
    row["states"] = [
        {k: (v if k == "state" else float(v)) for k, v in s.items()}
        for s in clean["states"]
    ]
    row["benefits"] = [
        {k: (v if k == "benefit_code_id" else float(v)) for k, v in b.items()}
        for b in clean["benefits"]
    ]
    return row, (clean if action != "error" else None)


# ---------------------------------------------------------------------------
# The SlowBooks template
# ---------------------------------------------------------------------------
def _is_template(header: list[str]) -> bool:
    names = {_norm(h) for h in header}
    return "employee" in names and "gross" in names


def _read_template(db: Session, rows: list[list[str]], reason) -> dict:
    header = [h.strip() for h in rows[0]]
    col = {_norm(h): i for i, h in enumerate(header) if h}
    warnings: list[str] = []
    known = {_norm(c) for c in TEMPLATE_COLUMNS}
    codes: dict[str, BenefitCode | None] = {}
    benefit_cols: list[tuple[int, str, str]] = []  # (index, code, side)
    for i, h in enumerate(header):
        if not h or _norm(h) in known:
            continue
        m_er = _BENEFIT_EMPLOYER_RE.match(h)
        m_ee = _BENEFIT_RE.match(h) if not m_er else None
        if m_er or m_ee:
            code = (m_er or m_ee).group(1).strip()
            side = "employer_amount" if m_er else "employee_amount"
            benefit_cols.append((i, code, side))
            if code.lower() not in codes:
                codes[code.lower()] = (
                    db.query(BenefitCode)
                    .filter(func.lower(BenefitCode.code) == code.lower())
                    .first()
                )
            continue
        warnings.append(f"Column {h!r} is not one the template reads; ignored.")

    def cell(r, name):
        i = col.get(_norm(name))
        return r[i].strip() if i is not None and i < len(r) else ""

    amount_cols = [c for c, _ in TEMPLATE_AMOUNT_COLUMNS] + ["Year", "As of"]
    entries: list[dict] = []
    by_key: dict[str, dict] = {}
    for n, r in enumerate(rows[1:], start=2):
        if not any((c or "").strip() for c in r):
            continue
        name = cell(r, "Employee")
        has_amounts = any(cell(r, c) for c in amount_cols) or any(
            i < len(r) and r[i].strip() for i, _, _ in benefit_cols
        )
        state = {f: cell(r, c) for c, f in TEMPLATE_STATE_COLUMNS}
        key = _person_key(name)
        prior = by_key.get(key) if key else (entries[-1] if entries else None)
        if not has_amounts and prior is not None:
            # a second state for the employee above
            if state["state"]:
                prior["payload"]["states"].append(
                    {
                        "state": state["state"],
                        "wages": _num(state["wages"]),
                        "withholding": _num(state["withholding"]),
                    }
                )
            continue
        entry = {"line": n, "name": name, "email": cell(r, "Email"), "errors": []}
        if not has_amounts:
            entry["errors"].append(
                "The row has no Year, As of or amounts, and no employee above "
                "it to add a state to."
            )
        year_s, as_of_s = cell(r, "Year"), cell(r, "As of")
        as_of = _parse_date(as_of_s)
        if as_of_s and as_of is None:
            entry["errors"].append(f"As of {as_of_s!r} is not a date.")
        year = None
        if year_s:
            try:
                year = int(year_s)
            except ValueError:
                entry["errors"].append(f"Year {year_s!r} is not a year.")
        elif as_of:
            year = as_of.year
        payload = {"year": year, "as_of": as_of, "states": [], "benefits": []}
        for c, f in TEMPLATE_AMOUNT_COLUMNS:
            payload[f] = _num(cell(r, c))
        if state["state"]:
            payload["states"].append(
                {
                    "state": state["state"],
                    "wages": _num(state["wages"]),
                    "withholding": _num(state["withholding"]),
                }
            )
        by_code: dict[str, dict] = {}
        for i, code, side in benefit_cols:
            value = _num(r[i]) if i < len(r) else None
            if value is None:
                continue
            bc = codes.get(code.lower())
            if bc is None:
                entry["errors"].append(f"Benefit code {code!r} does not exist.")
                continue
            by_code.setdefault(bc.code, {"benefit_code_id": bc.id})[side] = value
        payload["benefits"] = list(by_code.values())
        entry["payload"] = payload
        if key and key in by_key and by_key[key]["payload"].get("year") == year:
            entry["errors"].append(
                f"{name} is in the file twice for {year}; a second state goes "
                "on a row with only the state columns filled."
            )
        elif key:
            by_key[key] = entry
        entries.append(entry)

    plan_rows = []
    for e in entries:
        employee, err = _match_employee(db, e["name"], e["email"])
        errors = e["errors"] + ([err] if err else [])
        row, clean = _plan_row(
            db, e["line"], e["name"], employee, e["payload"], errors, [], reason
        )
        row["_clean"] = clean
        plan_rows.append(row)
    return {"format": "template", "rows": plan_rows, "errors": [], "warnings": warnings}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def plan_import(db: Session, text: str, reason: str | None = None) -> dict:
    """The plan for a file: {format, rows, errors, warnings}. Each row
    keeps its clean payload under "_clean" for apply_plan; the route drops
    it from the response."""
    rows = [r for r in csv.reader(io.StringIO(text))]
    first = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if first is None:
        return {
            "format": None,
            "rows": [],
            "errors": ["The file is empty."],
            "warnings": [],
        }
    rows = rows[first:]
    if _is_template(rows[0]):
        return _read_template(db, rows, reason)
    return {
        "format": None,
        "rows": [],
        "errors": [
            "The file is not the SlowBooks opening-balance template (an "
            "Employee and a Gross column). Download the template and fill it "
            "in."
        ],
        "warnings": [],
    }


def apply_plan(db: Session, plan: dict, reason: str | None = None) -> int:
    """Write every row of the plan that is not an error, under the same
    lock as the employee page; commits. Returns how many were written."""
    written = 0
    years: set[int] = set()
    for row in plan["rows"]:
        clean = row.get("_clean")
        if row["action"] == "error" or clean is None:
            continue
        po.save_opening(db, row["employee_id"], clean, reason)
        years.add(clean["year"])
        written += 1
    db.commit()
    for year in sorted(years):
        po.refresh_benefit_ytd(db, year)
    return written
