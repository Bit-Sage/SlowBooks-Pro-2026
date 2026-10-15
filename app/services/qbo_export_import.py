# ============================================================================
# Migrate from QuickBooks Online — without the Intuit API.
#
# QuickBooks Online's own Export data (the gear → Tools → Export data) hands
# every user a zip of Excel workbooks: the reports Balance Sheet, Profit and
# Loss, Trial Balance, General Ledger and Journal, and the lists Customers,
# Vendors and Employees. No Intuit developer app, no production keys, no
# questionnaire: the route Intuit's developer portal makes impractical for a
# bookkeeper connecting their own books (the API path, docs/setup-qbo.md,
# stays for those who have keys).
#
# This dialect feeds the shared migration engine (migration_common):
#   coa  the Balance Sheet and the P&L, one text — every leaf with a figure
#        is an account, typed by the section it sits under, a sub-account
#        named Parent:Child as QuickBooks writes it elsewhere
#   gl   the Journal — one block per transaction: a dated first line, its
#        split lines, a totals row, a blank row
#   tb   the Trial Balance — and the basis its footer names
#   customers / vendors — the contact lists, names as QuickBooks has them
#
# The bundle's quirks, read as they come: a leading blank column on the
# journal and the lists, every figure as formula text ("=23683.16", the
# engine's parse_amount strips it), labels indented by three spaces per
# level, "Total …" rows and report arithmetic (Net Income …) that are not
# accounts, memo-only rows inside a block, and a footer that says which
# basis the reports were run on. A Trial Balance on the cash basis cannot
# be checked against the Journal, which is accrual by nature: it is set
# aside with a note rather than mistaken for opening balances.
#
# Built against a real export (a 24-year company, 9,510 transactions, every
# block balanced to the cent); the fixtures in tests/fixtures/qbo_export/
# are invented books in that layout. Nothing of the real file is here.
# ============================================================================

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.models.contacts import Customer, Vendor
from app.services.migration_common import (
    dry_run_bundle,
    parse_amount,
    parse_date,
    run_import_bundle,
    strip_code_suffix,
)

LABEL = "QuickBooks Online export"
SOURCE_TYPE = "qbo_export_import"
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")

# Filenames as the export names them (Balance_sheet.xlsx …), and as people
# save them. Order matters: "journal" before "general" (General_ledger is
# the journal again, by account, and is left alone).
_KINDS = (
    ("balance_sheet", "coa"),
    ("balance sheet", "coa"),
    ("balancesheet", "coa"),
    ("profit_and_loss", "coa"),
    ("profit and loss", "coa"),
    ("profitandloss", "coa"),
    ("chart", "coa"),
    ("journal", "gl"),
    ("general_ledger", "skip"),
    ("general ledger", "skip"),
    ("generalledger", "skip"),
    ("trial", "tb"),
    ("customer", "customers"),
    ("vendor", "vendors"),
    ("supplier", "vendors"),
    ("employee", "skip"),
)

# The section a leaf sits under decides its type. "LIABILITIES AND EQUITY"
# decides nothing by itself; Liabilities and Equity below it do.
_SECTION_TYPES = {
    "assets": AccountType.ASSET,
    "liabilities": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "cost of goods sold": AccountType.EXPENSE,
    "expenses": AccountType.EXPENSE,
    "other expenses": AccountType.EXPENSE,
}
_KIND_SECTIONS = {"bank accounts": "bank", "credit cards": "credit_card"}
# The reports' own headings. A labelled row with no figure that is not one
# of these is a parent account whose children follow (QuickBooks shows a
# parent with no activity of its own as a bare heading over them).
_SECTIONS = {
    "assets",
    "current assets",
    "bank accounts",
    "accounts receivable",
    "other current assets",
    "fixed assets",
    "other assets",
    "liabilities and equity",
    "liabilities",
    "current liabilities",
    "accounts payable",
    "credit cards",
    "other current liabilities",
    "long-term liabilities",
    "equity",
    "income",
    "cost of goods sold",
    "expenses",
    "other income",
    "other expenses",
}
# Report arithmetic that carries a figure but is not an account.
_ARITHMETIC = {
    "net income",
    "gross profit",
    "net operating income",
    "net other income",
    "retained earnings",
}
_BASIS = re.compile(r"\b(cash|accrual)\s+basis\b", re.I)
# What parse_coa inferred about accounts the reports hide, for the dry run
# to say (the engine's parser contract returns accounts and errors only).
_COA_NOTES: list[str] = []


def classify_filename(name: str) -> str | None:
    lowered = (name or "").lower()
    for fragment, kind in _KINDS:
        if fragment in lowered:
            return kind
    return None


# ── rows ───────────────────────────────────────────────────────────────────


def _rows(text: str) -> list[list[str]]:
    """The file's rows as lists of cells, the delimiter sniffed from the
    first line, cells kept verbatim (a label's leading spaces are its
    indentation)."""
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _cell(row: list[str], i: int) -> str:
    return row[i] if i < len(row) else ""


# ── the chart: the balance sheet and the P&L ───────────────────────────────


def parse_coa(
    csv_text: str, also: tuple[str, ...] = (), ordered: tuple[str, ...] = ()
) -> tuple[list[dict], list[str]]:
    """Accounts from one or both reports (the route joins them). A row with
    a label and a figure is an account; a labelled row with no figure is a
    section (ASSETS, Bank Accounts …) or a parent whose own total follows.
    Indentation says what sits under what.

    ``also`` names accounts the books use (the Journal's, the Trial
    Balance's) that the reports may not show: QuickBooks Online leaves a
    zero-balance row off the Balance Sheet and the Profit and Loss. A
    sub-account of one the reports do show takes its parent's type; a
    top-level one takes the type of its neighbour in ``ordered`` (the
    Trial Balance's names, in the chart's order), and with neither it is
    named, for the person to add first."""
    accounts: list[dict] = []
    errors: list[str] = []
    notes = _COA_NOTES
    notes.clear()
    seen: set[str] = set()
    stack: list[tuple[int, str, bool]] = []  # (indent, name, is_account)
    in_report = False
    for row in _rows(csv_text):
        raw = _cell(row, 0)
        value = _cell(row, 1).strip()
        name = raw.strip()
        if not name:
            if value.lower() == "total":
                in_report = True  # the header row; a new report starts here
                stack = []
            continue
        if not in_report:
            continue  # the preamble: company, title, date range
        low = name.lower()
        if _BASIS.search(name) and value == "":
            in_report = False  # the footer: "… - Cash Basis"
            stack = []
            continue
        if (
            low.startswith("total ")
            or low in _ARITHMETIC
            or low in ("total", "total assets", "total liabilities and equity")
        ):
            continue
        if low.startswith("total"):  # "TOTAL ASSETS"
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        is_account = value != "" or low not in _SECTIONS
        if is_account:
            sections = [n.lower() for _, n, acct in stack if not acct]
            parents = [n for _, n, acct in stack if acct]
            acct_type = None
            for section in sections:
                if section in _SECTION_TYPES:
                    acct_type = _SECTION_TYPES[section]
            if acct_type is None:
                errors.append(
                    f"Chart: {name!r} sits under no section that names its type"
                )
            else:
                full = ":".join(parents + [name])
                if full.lower() not in seen:
                    seen.add(full.lower())
                    kind = next(
                        (k for s, k in _KIND_SECTIONS.items() if s in sections), None
                    )
                    accounts.append(
                        {
                            "code": None,
                            "name": full,
                            "type": acct_type,
                            "description": None,
                            "bank_kind": kind,
                            "parent": ":".join(parents) if parents else None,
                        }
                    )
        stack.append((indent, name, is_account))
    by_name = {a["name"].lower(): a for a in accounts}
    for name in also:
        key = name.strip().lower()
        if not key or key in by_name or key in _ARITHMETIC:
            continue
        parent = name.rsplit(":", 1)[0] if ":" in name else ""
        parent_spec = by_name.get(parent.lower()) if parent else None
        if parent_spec is not None:
            acct_type, parent_name = parent_spec["type"], parent_spec["name"]
        else:
            # The Trial Balance lists accounts in the chart's order, assets
            # through expenses: the nearest listed neighbour the reports do
            # show says what kind this one is.
            acct_type, parent_name = _type_from_neighbours(name, ordered, by_name), None
            if acct_type is None:
                errors.append(
                    f"Account {name!r} is in the books but not on the Balance Sheet "
                    f"or the Profit and Loss (QuickBooks Online leaves a zero-balance "
                    f"row off both): add it to the chart with its type, then import "
                    f"again"
                )
                continue
            notes.append(
                f"Account {name!r} is not on the Balance Sheet or the Profit and Loss; "
                f"from its place in the Trial Balance it is a{'n' if acct_type.value[0] in 'aeiou' else ''} "
                f"{acct_type.value} account. Correct it in Chart of Accounts if that is wrong."
            )
        spec = {
            "code": None,
            "name": name.strip(),
            "type": acct_type,
            "description": None,
            "bank_kind": None,
            "parent": parent_name,
        }
        accounts.append(spec)
        by_name[key] = spec
    return accounts, errors


def _type_from_neighbours(name: str, listed: tuple[str, ...], by_name: dict):
    """The type of the nearest account before ``name`` in ``listed`` (the
    Trial Balance's order) that the chart knows, else the nearest after."""
    lowered = [n.strip().lower() for n in listed]
    key = name.strip().lower()
    if key not in lowered:
        return None
    i = lowered.index(key)
    for j in list(range(i - 1, -1, -1)) + list(range(i + 1, len(lowered))):
        spec = by_name.get(lowered[j])
        if spec is not None:
            return spec["type"]
    return None


# ── the journal ────────────────────────────────────────────────────────────


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    """Journal blocks as the engine's groups: rows of {journal, date,
    account, description, reference, debit, credit}. A block begins with a
    dated line and ends at its totals row (no account, the sums) or at a
    blank row; memo-only rows in between are not lines."""
    rows = _rows(csv_text)
    errors: list[str] = []
    groups: list[list[dict]] = []
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if (
            "date" in cells
            and "account" in cells
            and ("debit" in cells or "credit" in cells)
        ):
            header_at = i
            break
    if header_at is None:
        return [], ["Journal: no header row with Date, Account, Debit and Credit"]
    header = [c.strip().lower() for c in rows[header_at]]

    def idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date, i_type, i_num, i_name = (
        idx("date"),
        idx("transaction type", "type"),
        idx("num", "no.", "number"),
        idx("name"),
    )
    i_memo, i_acct, i_debit, i_credit = (
        idx("memo/description", "memo", "description"),
        idx("account"),
        idx("debit"),
        idx("credit"),
    )
    if None in (i_date, i_acct, i_debit, i_credit):
        return [], ["Journal: the header row lacks Date, Account, Debit or Credit"]
    current: list[dict] | None = None
    block_name = block_type = block_num = ""
    counter: dict[str, int] = {}
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        date_raw = _cell(row, i_date).strip()
        ttype = _cell(row, i_type).strip() if i_type is not None else ""
        account = _cell(row, i_acct).strip()
        debit_raw = _cell(row, i_debit).strip()
        credit_raw = _cell(row, i_credit).strip()
        if date_raw and ttype:
            if current:
                groups.append(current)
            try:
                date = parse_date(date_raw, _DATE_FORMATS)
            except Exception as exc:  # DataProblem: an unreadable date
                errors.append(f"Journal row {n}: {exc}")
                current = None
                continue
            block_type = ttype
            block_num = _cell(row, i_num).strip() if i_num is not None else ""
            block_name = _cell(row, i_name).strip() if i_name is not None else ""
            key = f"{block_type} {block_num}".strip() + f" · {date.isoformat()}"
            counter[key] = counter.get(key, 0) + 1
            if counter[key] > 1:
                key = f"{key} #{counter[key]}"
            current = []
            current_date = date
            current_key = key[:100]
            current_desc = (
                block_type
                + (f" {block_num}" if block_num else "")
                + (f" — {block_name}" if block_name else "")
            )[:200]
        if current is None:
            continue
        if not account:
            if debit_raw or credit_raw:
                # A row with amounts and no account is the block's totals
                # row when it carries the block's sums; an item at 0.00
                # inside an invoice comes out the same way with a bare 0,
                # and is not the end of anything.
                sd = sum(r["debit"] for r in current)
                sc = sum(r["credit"] for r in current)
                try:
                    td, tc = parse_amount(debit_raw), parse_amount(credit_raw)
                except Exception:
                    errors.append(
                        f"Journal row {n}: unreadable amount beside no account"
                    )
                    continue
                if (td, tc) == (sd, sc) and current:
                    groups.append(current)
                    current = None
                elif td == 0 and tc == 0:
                    continue  # a zero line with no account
                else:
                    errors.append(
                        f"Journal {current_key}: a line with no account carries {td}/{tc}"
                    )
            continue  # a memo-only row (Subtotal, an address line), or a blank
        try:
            debit = parse_amount(debit_raw)
            credit = parse_amount(credit_raw)
        except Exception as exc:
            errors.append(f"Journal row {n}: {exc}")
            continue
        memo = _cell(row, i_memo).strip() if i_memo is not None else ""
        line_name = _cell(row, i_name).strip() if i_name is not None else ""
        current.append(
            {
                "journal": current_key,
                "date": current_date,
                "account": account,
                "description": (
                    current_desc
                    + (f": {memo}" if memo else "")
                    + (
                        f" ({line_name})"
                        if line_name and line_name != block_name
                        else ""
                    )
                )[:500],
                "reference": current_key,
                "debit": debit,
                "credit": credit,
            }
        )
    if current:
        groups.append(current)
    return groups, errors


# ── the trial balance ──────────────────────────────────────────────────────


def trial_balance_basis(csv_text: str) -> str | None:
    """ "cash" or "accrual" from the report's footer, else None."""
    for row in reversed(_rows(csv_text)):
        for cell in row:
            m = _BASIS.search(cell)
            if m:
                return m.group(1).lower()
    return None


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    balances: dict[str, Decimal] = {}
    errors: list[str] = []
    rows = _rows(csv_text)
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if "debit" in cells and "credit" in cells:
            header_at = i
            break
    if header_at is None:
        return {}, ["Trial balance: no header row with Debit and Credit"]
    header = [c.strip().lower() for c in rows[header_at]]
    i_debit, i_credit = header.index("debit"), header.index("credit")
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        name = _cell(row, 0).strip()
        if not name or name.lower().startswith("total") or _BASIS.search(name):
            continue
        try:
            net = parse_amount(_cell(row, i_debit)) - parse_amount(_cell(row, i_credit))
        except Exception as exc:
            errors.append(f"Trial balance row {n}: {exc}")
            continue
        balances[strip_code_suffix(name)] = net
    return balances, errors


# ── the lists ──────────────────────────────────────────────────────────────


def parse_contacts(csv_text: str, label: str) -> list[dict]:
    """Rows of the Customer or Vendor Contact List: {name, email, phone,
    address} with the list's own column names."""
    rows = _rows(csv_text)
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if label.lower() in cells and any("phone" in c or "email" in c for c in cells):
            header_at = i
            break
    if header_at is None:
        return []
    header = [c.strip().lower() for c in rows[header_at]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_name = col(label.lower())
    i_phone, i_email = col("phone numbers", "phone"), col("email")
    i_addr = col("billing address", "address")
    out = []
    for row in rows[header_at + 1 :]:
        name = _cell(row, i_name).strip()
        if not name:
            continue
        out.append(
            {
                "name": name[:200],
                "email": (_cell(row, i_email).strip() if i_email is not None else "")[
                    :200
                ],
                "phone": (_cell(row, i_phone).strip() if i_phone is not None else "")[
                    :50
                ],
                "address": (_cell(row, i_addr).strip() if i_addr is not None else ""),
            }
        )
    return out


def _phone(raw: str) -> tuple[str, str]:
    """ "Mobile: (555) 010-2001" → ("", "(555) 010-2001"); "Phone: …" → phone."""
    mobile = ""
    phone = raw
    m = re.match(r"(?i)^(mobile|cell)\s*:\s*(.+)$", raw)
    if m:
        return "", m.group(2).strip()
    m = re.match(r"(?i)^(phone|work|office)\s*:\s*(.+)$", raw)
    if m:
        phone = m.group(2).strip()
    return phone, mobile


def _address(raw: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return "", ""
    return lines[0][:200], ", ".join(lines[1:])[:200]


def _import_contacts(db: Session, bundle: dict) -> tuple[int, int]:
    created_customers = created_vendors = 0
    if "customers" in bundle:
        have = {c.name.lower() for c in db.query(Customer.name).all()}
        for spec in parse_contacts(bundle["customers"], "Customer"):
            if spec["name"].lower() in have:
                continue
            phone, mobile = _phone(spec["phone"])
            a1, a2 = _address(spec["address"])
            db.add(
                Customer(
                    name=spec["name"],
                    email=spec["email"] or None,
                    phone=phone or None,
                    mobile=mobile or None,
                    bill_address1=a1 or None,
                    bill_address2=a2 or None,
                )
            )
            have.add(spec["name"].lower())
            created_customers += 1
    if "vendors" in bundle:
        have = {v.name.lower() for v in db.query(Vendor.name).all()}
        for spec in parse_contacts(bundle["vendors"], "Vendor"):
            if spec["name"].lower() in have:
                continue
            phone, mobile = _phone(spec["phone"])
            a1, a2 = _address(spec["address"])
            vendor = Vendor(
                name=spec["name"], email=spec["email"] or None, phone=phone or None
            )
            for attr, value in (
                ("mobile", mobile),
                ("address1", a1),
                ("address2", a2),
                ("bill_address1", a1),
                ("bill_address2", a2),
            ):
                if value and hasattr(Vendor, attr):
                    setattr(vendor, attr, value)
            db.add(vendor)
            have.add(spec["name"].lower())
            created_vendors += 1
    db.flush()
    return created_customers, created_vendors


# ── the engine's hooks ─────────────────────────────────────────────────────


def _names_in_use(bundle: dict) -> tuple[str, ...]:
    """Every account the Journal posts to and the Trial Balance lists, in
    first-seen order, so the chart parser can fill what the reports hide."""
    seen: dict[str, None] = {}
    tb_text = bundle.get("tb") or bundle.get("tb_names_only")
    if tb_text:  # first: its order is the chart's, assets through expenses
        for name in _tb_names(tb_text):
            seen.setdefault(name, None)
    if "gl" in bundle:
        groups, _ = parse_gl(bundle["gl"])
        for group in groups:
            for row in group:
                seen.setdefault(row["account"], None)
    return tuple(seen)


def _tb_names(csv_text: str) -> list[str]:
    """The Trial Balance's account names as written, after its header row
    and before its footer; nothing from the preamble."""
    names: list[str] = []
    started = False
    for row in _rows(csv_text):
        cells = [c.strip().lower() for c in row]
        if not started:
            started = "debit" in cells and "credit" in cells
            continue
        name = _cell(row, 0).strip()
        if name and not name.lower().startswith("total") and not _BASIS.search(name):
            names.append(name)
    return names


def _parsers(bundle: dict) -> dict:
    also = _names_in_use(bundle)
    tb_text = bundle.get("tb") or bundle.get("tb_names_only")
    ordered = tuple(_tb_names(tb_text)) if tb_text else ()
    return {
        "coa": lambda text: parse_coa(text, also, ordered),
        "gl": parse_gl,
        "tb": parse_tb,
    }


PARSERS = {"coa": parse_coa, "gl": parse_gl, "tb": parse_tb}


def _prepare(bundle: dict) -> tuple[dict, list[str]]:
    """The bundle the engine sees, and the notes to add. A cash-basis Trial
    Balance is set aside: the Journal is accrual, and the engine would read
    the difference (open invoices, unpaid bills) as opening balances."""
    bundle = dict(bundle)
    notes: list[str] = []
    if "tb" in bundle and trial_balance_basis(bundle["tb"]) == "cash":
        bundle["tb_names_only"] = bundle.pop("tb")
        bundle["tb_set_aside"] = True  # the engine's "no trial balance" note stays out
        notes.append(
            "The Trial Balance was exported on the cash basis, and the Journal is "
            "accrual, so the two cannot be compared: balances were not verified. "
            "To verify, set the company's report basis to Accrual in QuickBooks "
            "Online, export again, and dry-run with that Trial Balance."
        )
    return bundle, notes


def _counts(db: Session, bundle: dict) -> dict:
    """The customers and vendors an import would make: the lists' names not
    in the books yet, as _import_contacts counts them."""

    def new(kind, label, model):
        if kind not in bundle:
            return 0
        have = {row.name.lower() for row in db.query(model.name).all()}
        names = {spec["name"].lower() for spec in parse_contacts(bundle[kind], label)}
        return len(names - have)

    return {
        "customers": new("customers", "Customer", Customer),
        "vendors": new("vendors", "Vendor", Vendor),
    }


def dry_run(db: Session, bundle: dict) -> dict:
    prepared, notes = _prepare(bundle)
    verdict = dry_run_bundle(db, prepared, _parsers(prepared), LABEL, SOURCE_TYPE)
    verdict["warnings"] = notes + list(_COA_NOTES) + verdict["warnings"]
    verdict.update(_counts(db, bundle))
    return verdict


def run_import(db: Session, bundle: dict) -> dict:
    prepared, notes = _prepare(bundle)
    result = run_import_bundle(db, prepared, _parsers(prepared), SOURCE_TYPE, LABEL)
    result["warnings"] = notes + list(_COA_NOTES) + result["warnings"]
    result.update(_counts(db, bundle))
    if result["ok"]:
        customers, vendors = _import_contacts(db, bundle)
        db.commit()
    else:
        customers = vendors = 0
    result["imported_customers"] = customers
    result["imported_vendors"] = vendors
    return result
