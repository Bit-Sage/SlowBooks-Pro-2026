# ============================================================================
# The contract on the job (#552) — the owner's side of a construction job.
#
# A construction job can carry a contract: a lump sum, a GMP (guaranteed
# maximum price) or cost-plus, with a fee, a cap, contingency and retainage.
# The owner's schedule of values (SOV) breaks the contract sum into the lines
# a pay application bills against. This module builds the SOV from an accepted
# estimate and rolls the contract up into the figures the Contract tab reads.
#
# The math here is pure where it can be (sov_from_estimate, the summary
# arithmetic); build_sov is the one DB writer. Later steps plug in here:
# #553 buyout/commitments, #554 change orders (fills "changes" and
# "contingency_used"), #555 progress billing from the SOV, #556 WIP reports.
# ============================================================================

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job, SovLine
from app.services.accounting import _q
from app.services.estimating import _counts_as_base, line_price


def sov_from_estimate(estimate: Estimate, totals: dict) -> list[dict]:
    """The SOV lines an accepted estimate produces, in print order:

    - one `work` line per trade package (description = package name;
      scheduled value = Σ line_price of that package's base and
      accepted-alternate lines, which after #551's reprice already carry
      the markups), packages named first then "Unassigned";
    - one `allowance` line per allowance line (its own amount);
    - one `contingency` line when the job set a contingency amount — added
      by build_sov, which knows the job, not here.

    The SOV never itemises the estimate's markups: they are inside the work
    values. Returns plain dicts (no line_no / item_no yet).
    """
    work: dict = {}  # package_id -> {"name", "value"}
    allowances: list[dict] = []
    for line in estimate.lines:
        kind = line.line_kind or "base"
        if kind == "allowance":
            allowances.append(
                {
                    "item_no": None,
                    "description": line.description or "Allowance",
                    "trade_package_id": line.trade_package_id,
                    "cost_code_id": line.cost_code_id,
                    "kind": "allowance",
                    "scheduled_value": line_price(line),
                    "source_estimate_id": estimate.id,
                }
            )
            continue
        if not _counts_as_base(line):
            continue  # unit prices and un-accepted alternates are in no line
        key = line.trade_package_id
        bucket = work.get(key)
        if bucket is None:
            bucket = {
                "name": (
                    line.trade_package.name
                    if line.trade_package is not None
                    else "Unassigned"
                ),
                "value": Decimal("0"),
            }
            work[key] = bucket
        bucket["value"] += line_price(line)

    # Named packages by name, Unassigned (None key) last — the estimate's order.
    ordered = sorted(
        work.items(),
        key=lambda kv: (kv[0] is None, kv[1]["name"].lower()),
    )
    lines = [
        {
            "item_no": None,
            "description": bucket["name"],
            "trade_package_id": pkg_id,
            "cost_code_id": None,
            "kind": "work",
            "scheduled_value": bucket["value"],
            "source_estimate_id": estimate.id,
        }
        for pkg_id, bucket in ordered
    ]
    lines.extend(allowances)
    return lines


def _numbered(lines: list[dict]) -> list[dict]:
    """Stamp 1-based line_no and a zero-padded item_no where blank."""
    for n, line in enumerate(lines, start=1):
        line["line_no"] = n
        if not line.get("item_no"):
            line["item_no"] = f"{n:02d}"
    return lines


def build_sov(db: Session, job: Job, estimate: Estimate, replace: bool = True) -> list:
    """Write the SOV from an accepted estimate. 409 if the estimate isn't
    this job's or isn't accepted. Replaces any existing SOV lines by
    default."""
    if estimate.status != EstimateStatus.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_not_accepted",
                "message": "Build the schedule of values from an accepted estimate.",
            },
        )
    if estimate.job_id != job.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_job_mismatch",
                "message": "That estimate is not on this job.",
            },
        )
    from app.services.estimating import estimate_totals

    lines = sov_from_estimate(estimate, estimate_totals(estimate))
    # The owner contingency carried inside the contract is its own SOV line.
    if job.contingency_amount is not None:
        lines.append(
            {
                "item_no": None,
                "description": "Contingency",
                "trade_package_id": None,
                "cost_code_id": None,
                "kind": "contingency",
                "scheduled_value": _q(job.contingency_amount),
                "source_estimate_id": estimate.id,
            }
        )
    _numbered(lines)
    if replace:
        db.query(SovLine).filter(SovLine.job_id == job.id).delete()
        db.flush()
    rows = []
    for line in lines:
        row = SovLine(job_id=job.id, **line)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def contract_summary(db: Session, job: Job) -> dict:
    """The contract roll-up for one job. Actual cost and billed-to-date come
    from the same posted lines the job overview reads, so the Contract tab
    always agrees with the rest of the job page."""
    from app.services.job_costing import job_cost_tree

    totals = job_cost_tree(db, job.id)["totals"]
    cost = Decimal(str(totals["actual"]))
    billed = Decimal(str(totals["act_revenue"]))

    ctype = job.contract_type
    amount = job.contract_amount
    original = job.original_contract_amount
    changes = (
        _q(amount - original)
        if amount is not None and original is not None
        else Decimal("0")
    )

    # Fee: a fixed fee wins; otherwise a percent of actual cost for cost-plus
    # and GMP; lump sum earns no separate fee.
    if job.fee_amount is not None:
        fee = _q(job.fee_amount)
    elif ctype in ("cost_plus", "gmp") and job.fee_pct is not None:
        fee = _q(cost * Decimal(str(job.fee_pct)) / Decimal("100"))
    else:
        fee = Decimal("0")

    cap = job.cap_amount
    cap_remaining = _q(cap - (cost + fee)) if cap is not None else None

    contingency = job.contingency_amount
    contingency_used = Decimal("0")  # #554 fills this
    contingency_remaining = (
        _q(contingency - contingency_used) if contingency is not None else None
    )

    sov_total = sum(
        (Decimal(str(r.scheduled_value)) for r in job.sov_lines), Decimal("0")
    )
    sov_balance = _q((amount or Decimal("0")) - sov_total)
    billed_pct = float(billed / amount * 100) if amount else None

    def f(v):
        return float(v) if v is not None else None

    return {
        "contract_type": ctype,
        "contract_amount": f(amount),
        "original_contract_amount": f(original),
        "changes": float(changes),
        "fee": float(fee),
        "fee_pct": f(job.fee_pct),
        "fee_amount": f(job.fee_amount),
        "cost_to_date": float(cost),
        "cap_amount": f(cap),
        "cap_remaining": f(cap_remaining),
        "contingency_amount": f(contingency),
        "contingency_used": float(contingency_used),
        "contingency_remaining": f(contingency_remaining),
        "retainage_pct": f(job.retainage_pct),
        "contract_date": job.contract_date,
        "contract_estimate_id": job.contract_estimate_id,
        "sov_total": float(sov_total),
        "sov_balance": float(sov_balance),
        "billed_to_date": float(billed),
        "billed_pct": billed_pct,
    }
