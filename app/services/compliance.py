# ============================================================================
# Sub vendor compliance (#553) — the paperwork flags the bills list, Pay Bills
# and the job Buyout tab read. Warn-only: nothing here blocks a payment.
# ============================================================================

from datetime import date as _date


def vendor_compliance(vendor, today: _date | None = None) -> dict:
    """What a Sub vendor's paperwork looks like at pay time: whether a W-9 is
    on file and whether the certificate of insurance is in hand. coi_expired
    is true for a Sub whose COI is past OR missing; coi_expires is an ISO
    string (or null) so the shape is the same for JSON and for the bills
    schema. A non-Sub vendor is never flagged."""
    today = today or _date.today()
    is_sub = bool(vendor and vendor.vendor_type == "sub")
    coi = vendor.coi_expires if vendor else None
    coi_expired = is_sub and (coi is None or today > coi)
    return {
        "is_sub": is_sub,
        "w9_on_file": bool(vendor and vendor.w9_on_file),
        "coi_expires": coi.isoformat() if coi else None,
        "coi_expired": coi_expired,
    }
