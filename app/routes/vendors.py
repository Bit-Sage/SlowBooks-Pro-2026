from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.schemas.contacts import VendorCreate, VendorUpdate, VendorResponse
from app.routes._helpers import get_or_404, reject_null_updates
from app.services.contact_balances import ZERO, vendor_balances
from app.services.duplicate_detection import find_duplicates
from app.services.form_1099 import clear_type_unless_1099
from app.services.vendor_bank_rule import set_vendor_rule, vendor_rules

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


def clear_sub_fields_unless_sub(vendor: Vendor) -> None:
    """The license and certificate-of-insurance fields describe a
    subcontractor; on any other vendor they are cleared rather than kept
    as data the forms would never show (mirrors clear_type_unless_1099)."""
    if vendor.vendor_type != "sub":
        vendor.license_number = None
        vendor.coi_carrier = None
        vendor.coi_policy_number = None
        vendor.coi_expires = None


def nec_for_sub(vendor: Vendor) -> None:
    """Turn on 1099-NEC when a vendor is first marked a Sub — a sub's labor
    is reportable — but only when its 1099 type is still blank, so a Sub
    already on 1099-MISC (or INT/DIV) keeps it. Call this on the transition
    into Sub, never on every write: an incorporated Sub taken off 1099 later
    must stay off. Mirrors the vendor form's toggleSubFields (#431)."""
    if vendor.vendor_type == "sub" and not vendor.vendor_1099_type:
        vendor.is_1099_vendor = True
        vendor.vendor_1099_type = "NEC"


def _responses(db: Session, vendors: list[Vendor]) -> list[VendorResponse]:
    """The vendors with what is owed to each, summed from the open bills,
    credits and unapplied payments (Vendor.balance is never written)."""
    balances = vendor_balances(db, [v.id for v in vendors])
    rules = vendor_rules(db, [v.id for v in vendors])
    out = []
    for v in vendors:
        resp = VendorResponse.model_validate(v)
        resp.balance = balances.get(v.id, ZERO)
        if v.id in rules:
            resp.bank_feed_rule = True
            resp.bank_rule_id = rules[v.id].id
        out.append(resp)
    return out


def _response(db: Session, vendor: Vendor) -> VendorResponse:
    return _responses(db, [vendor])[0]


@router.get("", response_model=list[VendorResponse])
def list_vendors(
    active_only: bool = False,
    inactive_only: bool = False,
    search: str = None,
    db: Session = Depends(get_db),
):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active)
    if inactive_only:  # the list page's Inactive view (#210)
        q = q.filter(Vendor.is_active.is_(False))
    if search:
        q = q.filter(Vendor.name.ilike(f"%{search}%"))
    return _responses(db, q.order_by(Vendor.name).all())


@router.get("/check-duplicate")
def check_duplicate(
    name: str = Query(..., min_length=1), db: Session = Depends(get_db)
):
    """Phase 11: standalone duplicate-check endpoint for pre-submit UI warnings."""
    existing = db.query(Vendor).filter(Vendor.is_active == True).all()  # noqa
    return {"duplicates": find_duplicates(name, existing)}


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    return _response(db, get_or_404(db, Vendor, vendor_id))


@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(
    data: VendorCreate,
    force: bool = Query(False, description="Bypass duplicate-name warning"),
    db: Session = Depends(get_db),
):
    if not force:
        existing = db.query(Vendor).filter(Vendor.is_active == True).all()  # noqa
        dupes = find_duplicates(data.name, existing)
        if dupes:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "possible_duplicate",
                    "message": "A similarly-named vendor already exists. Pass ?force=true to create anyway.",
                    "duplicates": dupes,
                },
            )
    vendor = Vendor(**data.model_dump())
    # A new vendor marked a Sub turns on 1099-NEC, unless the client set the
    # 1099 answer itself — the form always does, and an API caller may say
    # "false" for an incorporated sub. Only an API create that leaves the
    # 1099 answer out gets the default.
    if "is_1099_vendor" not in data.model_dump(exclude_unset=True):
        nec_for_sub(vendor)
    clear_type_unless_1099(vendor)
    clear_sub_fields_unless_sub(vendor)
    db.add(vendor)
    if data.bank_feed_rule:
        db.flush()
        set_vendor_rule(db, vendor, True)
    db.commit()
    db.refresh(vendor)
    return _response(db, vendor)


@router.put("/{vendor_id}", response_model=VendorResponse)
def update_vendor(vendor_id: int, data: VendorUpdate, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    was_sub = vendor.vendor_type == "sub"
    update_data = reject_null_updates(Vendor, data.model_dump(exclude_unset=True))
    for key, val in update_data.items():
        setattr(vendor, key, val)
    # Marking a vendor a Sub for the first time turns on 1099-NEC (blank type
    # only). Re-saving a Sub, or saving one with 1099 deliberately off, is
    # left alone — the rule fires on the transition, not on every write.
    if "vendor_type" in update_data and not was_sub:
        nec_for_sub(vendor)
    clear_type_unless_1099(vendor)
    clear_sub_fields_unless_sub(vendor)
    if data.bank_feed_rule is not None:
        set_vendor_rule(db, vendor, data.bank_feed_rule)
    db.commit()
    db.refresh(vendor)
    return _response(db, vendor)


@router.delete("/{vendor_id}")
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    vendor.is_active = False
    db.commit()
    return {"message": "Vendor deactivated"}


def bank_entries_for(
    db: Session,
    vendor_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """The register entries that name this vendor (#491) — lines added
    from a bank feed — oldest first. `amount` is what was paid: money out
    of the bank or onto the card is positive, a refund back negative."""
    from app.models.transactions import Transaction
    from app.services.bank_register import source_link, voided_transaction_ids

    q = db.query(Transaction).filter(
        Transaction.vendor_id == vendor_id, Transaction.source_type == "bank_entry"
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    txns = q.order_by(Transaction.date, Transaction.id).all()
    voided = voided_transaction_ids(db, [t.id for t in txns])
    out = []
    for t in txns:
        bank = next((ln for ln in t.lines if ln.account and ln.account.bank_kind), None)
        if bank is None:
            continue
        memo = next((ln.description for ln in t.lines if ln.description), "") or ""
        out.append(
            {
                "transaction_id": t.id,
                "date": t.date.isoformat(),
                "reference": t.reference or "",
                "memo": memo,
                "account_id": bank.account_id,
                "account_name": bank.account.name,
                "amount": str(
                    Decimal(str(bank.credit or 0)) - Decimal(str(bank.debit or 0))
                ),
                "is_voided": t.id in voided,
                "link": source_link(t),
            }
        )
    return out


@router.get("/{vendor_id}/bank-entries")
def vendor_bank_entries(
    vendor_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """What was paid to this vendor straight from a bank or card account
    — lines added from a bank feed (#491) — newest first."""
    get_or_404(db, Vendor, vendor_id)
    return list(reversed(bank_entries_for(db, vendor_id, start_date, end_date)))


@router.get("/{vendor_id}/payments/csv")
def vendor_payments_csv(
    request: Request,
    vendor_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """The vendor's payments as a CSV, with a paid-to-date total (#335):
    what a client asks for ("how much have we paid them this year")."""
    import io

    from app.models.bills import BillPayment
    from app.routes.csv import _csv_response
    from app.services.csv_export import _money_cell, _SafeWriter
    from app.services.request_utils import file_name

    vendor = get_or_404(db, Vendor, vendor_id)
    q = db.query(BillPayment).filter(BillPayment.vendor_id == vendor_id)
    if start_date:
        q = q.filter(BillPayment.date >= start_date)
    if end_date:
        q = q.filter(BillPayment.date <= end_date)
    payments = q.order_by(BillPayment.date, BillPayment.id).all()
    # bill payments and the bank-feed entries naming the vendor (#491), by date
    rows = [(p.date.isoformat(), 0, p.id, p) for p in payments] + [
        (e["date"], 1, e["transaction_id"], e)
        for e in bank_entries_for(db, vendor_id, start_date, end_date)
    ]
    rows.sort(key=lambda r: r[:3])
    out = io.StringIO()
    w = _SafeWriter(out)  # formula guard; amounts stay numbers
    w.writerow(["Vendor", vendor.name])
    w.writerow(["Period", f"{start_date or 'start'} to {end_date or 'today'}"])
    w.writerow([])
    w.writerow(["Date", "Method", "Check #", "Amount", "Paid from", "Notes", "Voided"])
    total = Decimal("0")
    for _, kind, _, p in rows:
        if kind == 1:
            amount = Decimal(p["amount"])
            if not p["is_voided"]:
                total += amount
            w.writerow(
                [
                    p["date"],
                    "Bank entry",
                    p["reference"],
                    _money_cell(amount),
                    p["account_name"],
                    p["memo"].replace("\n", " "),
                    "yes" if p["is_voided"] else "",
                ]
            )
            continue
        amount = Decimal(str(p.amount or 0))
        if not p.is_voided:
            total += amount
        w.writerow(
            [
                p.date.isoformat(),
                p.method or "",
                p.check_number or "",
                _money_cell(amount),
                (p.pay_from_account.name if p.pay_from_account else ""),
                (p.notes or "").replace("\n", " "),
                "yes" if p.is_voided else "",
            ]
        )
    w.writerow(["", "", "Total paid", _money_cell(total), "", "", ""])
    # content_disposition makes the name safe for the header (ASCII fallback
    # plus filename*=UTF-8''…), so a non-Latin-1 vendor ("Łódź Supply") no
    # longer 500s; request lets the desktop shim get its inline header.
    return _csv_response(
        out.getvalue(),
        file_name("Payments", vendor.name) + ".csv",
        request,
    )
