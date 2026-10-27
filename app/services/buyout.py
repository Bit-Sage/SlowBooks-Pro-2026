# ============================================================================
# Buyout and commitments (#553) — bid vs buyout per trade package.
#
# A commitment is a purchase order (plain PO or subcontract); its lines are
# its schedule of values. This builds the job's buyout table: what each trade
# package was bid at (from the accepted estimate), what has been bought (the
# commitments on the job), and the variance, with committed-remaining and
# billed-to-date alongside. #556 turns this into the standalone buyout report
# and job-cost-by-vendor; this endpoint is the seam.
# ============================================================================

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.services.accounting import _q
from app.services.compliance import vendor_compliance
from app.services.estimating import estimate_totals

# A commitment counts once it has left draft; its remaining (unbilled) part
# counts only while it is still open.
_NON_DRAFT = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED, POStatus.CLOSED)
_OPEN = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)

_UNASSIGNED = "Unassigned"


def _bid_estimate(db: Session, job: Job):
    """The estimate the bid figures come from: the contract's source estimate
    if the job names one, else an accepted estimate on the job, else none."""
    if job.contract_estimate_id:
        est = db.get(Estimate, job.contract_estimate_id)
        if est:
            return est
    return (
        db.query(Estimate)
        .filter(Estimate.job_id == job.id, Estimate.status == EstimateStatus.ACCEPTED)
        .order_by(Estimate.id)
        .first()
    )


def job_buyout(db: Session, job: Job) -> dict:
    """One row per trade package: the bid cost, what has been bought, the
    variance, committed-remaining, billed-to-date and the commitments behind
    it. An 'Unassigned' row gathers commitments with no package (and any bid
    cost with no package); a totals row foots the table."""
    # Bid cost per package from the accepted estimate.
    bid_by_pkg: dict = {}  # package_id (or None) -> {"name", "cost"}
    est = _bid_estimate(db, job)
    if est is not None:
        for row in estimate_totals(est)["by_package"]:
            bid_by_pkg[row["package_id"]] = {
                "name": row["package_name"],
                "cost": _q(row["cost"]),
            }

    # Commitments on the job (header job), non-draft.
    commitments = (
        db.query(PurchaseOrder)
        .filter(
            PurchaseOrder.job_id == job.id,
            PurchaseOrder.status.in_(_NON_DRAFT),
        )
        .order_by(PurchaseOrder.po_number)
        .all()
    )

    # Accumulate per package.
    packages: dict = {}  # package_id (or None) -> row dict

    def row_for(pkg_id, name):
        row = packages.get(pkg_id)
        if row is None:
            row = {
                "package_id": pkg_id,
                "package_name": name,
                "bid_cost": Decimal("0"),
                "bought": Decimal("0"),
                "committed_remaining": Decimal("0"),
                "billed_to_date": Decimal("0"),
                "commitments": [],
            }
            packages[pkg_id] = row
        return row

    # Seed the bid rows first so a package bid but not bought still shows.
    for pkg_id, info in bid_by_pkg.items():
        name = info["name"] if pkg_id is not None else _UNASSIGNED
        row_for(pkg_id, name)["bid_cost"] = info["cost"]

    for po in commitments:
        pkg_id = po.trade_package_id
        name = (
            po.trade_package.name
            if po.trade_package is not None
            else (bid_by_pkg.get(None, {}).get("name") or _UNASSIGNED)
        )
        row = row_for(pkg_id, name if pkg_id is not None else _UNASSIGNED)
        # Bought is the pre-tax subtotal, so it compares like-for-like with the
        # bid cost; tax is payable in full and is not a buyout figure.
        subtotal = _q(po.subtotal or 0)
        billed = _q(
            sum((Decimal(str(ln.billed_amount or 0)) for ln in po.lines), Decimal("0"))
        )
        remaining = Decimal("0")
        if po.status in _OPEN:
            remaining = _q(
                sum(
                    (
                        Decimal(str(ln.amount or 0))
                        - Decimal(str(ln.billed_amount or 0))
                        for ln in po.lines
                    ),
                    Decimal("0"),
                )
            )
        row["bought"] += subtotal
        row["committed_remaining"] += remaining
        row["billed_to_date"] += billed
        row["commitments"].append(
            {
                "id": po.id,
                "number": po.po_number,
                "vendor_name": po.vendor.name if po.vendor else "",
                "type": po.commitment_type,
                "subtotal": float(subtotal),
                "billed": float(billed),
                "status": (
                    po.status.value if hasattr(po.status, "value") else po.status
                ),
                "compliance": vendor_compliance(po.vendor),
            }
        )

    # Named packages by name, Unassigned last.
    ordered = sorted(
        packages.values(),
        key=lambda r: (r["package_id"] is None, (r["package_name"] or "").lower()),
    )
    rows = []
    totals = {
        "bid_cost": Decimal("0"),
        "bought": Decimal("0"),
        "variance": Decimal("0"),
        "committed_remaining": Decimal("0"),
        "billed_to_date": Decimal("0"),
    }
    for r in ordered:
        variance = _q(r["bid_cost"] - r["bought"])
        totals["bid_cost"] += r["bid_cost"]
        totals["bought"] += r["bought"]
        totals["variance"] += variance
        totals["committed_remaining"] += r["committed_remaining"]
        totals["billed_to_date"] += r["billed_to_date"]
        rows.append(
            {
                "package_id": r["package_id"],
                "package_name": r["package_name"],
                "bid_cost": float(r["bid_cost"]),
                "bought": float(r["bought"]),
                "variance": float(variance),
                "committed_remaining": float(r["committed_remaining"]),
                "billed_to_date": float(r["billed_to_date"]),
                "commitments": r["commitments"],
            }
        )

    return {
        "job_id": job.id,
        "bid_estimate_id": est.id if est is not None else None,
        "rows": rows,
        "totals": {k: float(v) for k, v in totals.items()},
    }
