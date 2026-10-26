from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.schemas.contacts import VendorCreate, VendorUpdate, VendorResponse
from app.routes._helpers import get_or_404, reject_null_updates
from app.services.contact_balances import ZERO, vendor_balances
from app.services.duplicate_detection import find_duplicates
from app.services.form_1099 import clear_type_unless_1099
from app.services.vendor_bank_rule import set_vendor_rule, vendor_rules
from app.services.vendor_payments import bank_entries_for, vendor_payment_history

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
    if search:  # name, company, email or phone, as the list's box filters (#569)
        like = f"%{search}%"
        q = q.filter(
            or_(
                Vendor.name.ilike(like),
                Vendor.company.ilike(like),
                Vendor.email.ilike(like),
                Vendor.phone.ilike(like),
            )
        )
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


@router.get("/{vendor_id}/bank-entries")
def vendor_bank_entries(
    vendor_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """What was paid to this vendor straight from a bank or card account
    — lines added from a bank feed (#491) — newest first. The page reads
    them through the payment history now (#569); kept for API readers."""
    get_or_404(db, Vendor, vendor_id)
    return list(reversed(bank_entries_for(db, vendor_id, start_date, end_date)))


@router.get("/{vendor_id}/payment-history")
def vendor_payment_history_route(
    vendor_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Every bill payment, direct expense and bank-feed entry to the vendor
    (#569), oldest first with a running total, subtotals by kind, and the
    all-time total beside the range's. No dates is year to date; one date
    leaves the other side open."""
    get_or_404(db, Vendor, vendor_id)
    if start_date is None and end_date is None:
        today = date.today()
        start_date, end_date = date(today.year, 1, 1), today
    out = vendor_payment_history(db, vendor_id, start_date, end_date)
    out["all_time"] = vendor_payment_history(db, vendor_id)["total"]
    return out


@router.get("/{vendor_id}/payments/csv")
def vendor_payments_csv(
    request: Request,
    vendor_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """The vendor's payment history as a CSV (#335, #569), through the CSV
    export service: what a client asks for ("how much have we paid them
    this year"). No dates is all time; the page sends its range."""
    from app.routes.csv import _csv_response
    from app.services.csv_export import export_vendor_payments
    from app.services.request_utils import file_name

    vendor = get_or_404(db, Vendor, vendor_id)
    # content_disposition makes the name safe for the header (ASCII fallback
    # plus filename*=UTF-8''…), so a non-Latin-1 vendor ("Łódź Supply") no
    # longer 500s; request lets the desktop shim get its inline header.
    return _csv_response(
        export_vendor_payments(db, vendor_id, start_date, end_date),
        file_name("Payments", vendor.name) + ".csv",
        request,
    )
