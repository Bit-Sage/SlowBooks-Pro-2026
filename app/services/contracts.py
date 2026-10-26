# ============================================================================
# The contract on the job (#552) — the owner's side of a construction job.
#
# A construction job can carry a contract: a lump sum, a GMP (guaranteed
# maximum price) or cost-plus, with a fee, a cap, contingency and retainage.
# The owner's schedule of values (SOV) breaks the contract sum into the lines
# a pay application bills against. This module builds the SOV from an accepted
# estimate and rolls the contract up into the figures the Contract tab reads.
#
# The contract sum is PRE-TAX — the SOV foots to the estimate's pre-tax bid
# (tax is added on each invoice, #555). The math here is pure where it can be
# (sov_from_estimate, the summary arithmetic); build_sov is the one DB writer.
# Later steps plug in here: #553 buyout/commitments, #554 change orders (fills
# "changes" and "contingency_used"), #555 progress billing, #556 WIP reports.
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job, SovLine
from app.services.accounting import _q
from app.services.estimating import (
    estimate_totals,
    fmt_pct,
    included_lines,
    line_price,
)


def sov_from_estimate(estimate: Estimate) -> list[dict]:
    """The SOV lines an accepted estimate produces, in print order:

    - one `work` line per trade package — the sum of that package's base and
      accepted-alternate line PRICES, now AT COST (the lines carry no markup),
      packages named first then "Unassigned";
    - one `allowance` line per allowance line (its own price);
    - one line per NON-ZERO markup, in order — general_conditions, insurance,
      fee, bond — each its own kind, named "General conditions (8%)" style.

    The work lines plus the allowance lines sum to the estimate's subtotal, and
    the markup lines add markup_total, so the whole set foots exactly to the
    estimate's pre-tax bid_total. The contingency line is added by build_sov,
    which knows the job. Returns plain dicts (no line_no / item_no yet)."""
    work: dict = {}  # package_id -> {"name", "value"}
    allowances: list[dict] = []
    for line in included_lines(estimate):
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
        # a base line or an accepted alternate — work scope, grouped by package
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
        # an accepted deduct alternate carries a negative price; it nets in here
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

    # One line per non-zero markup, in compounding order, each its own kind.
    # Work + allowance lines foot to the subtotal; these add markup_total, so
    # the full set foots to the estimate's pre-tax bid_total.
    totals = estimate_totals(estimate)
    markup_specs = (
        ("general_conditions", "General conditions", totals["gc_pct"], totals["gc"]),
        ("insurance", "Insurance", totals["insurance_pct"], totals["insurance"]),
        ("fee", "Fee", totals["fee_pct"], totals["fee"]),
        ("bond", "Bond", totals["bond_pct"], totals["bond"]),
    )
    for kind, label, pct, amount in markup_specs:
        if not amount:
            continue
        lines.append(
            {
                "item_no": None,
                "description": f"{label} ({fmt_pct(pct)}%)",
                "trade_package_id": None,
                "cost_code_id": None,
                "kind": kind,
                "scheduled_value": amount,
                "source_estimate_id": estimate.id,
            }
        )
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
    lines = sov_from_estimate(estimate)
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
        # TODO (#555): refuse 409 sov_has_billings once a pay application has
        # billed these lines — replacing a billed schedule would orphan the
        # pay history. Nothing bills the SOV yet, so a plain replace is safe.
        db.query(SovLine).filter(SovLine.job_id == job.id).delete()
        db.flush()
    rows = []
    for line in lines:
        row = SovLine(job_id=job.id, **line)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def sync_contingency_line(db: Session, job: Job) -> None:
    """Keep the SOV's contingency line in step with the contract term: update
    its value, create it when a contingency is set and there is none, remove
    it when the contingency is cleared. Renumbers the schedule afterwards."""
    existing = [ln for ln in job.sov_lines if ln.kind == "contingency"]
    if job.contingency_amount is None:
        for ln in existing:
            db.delete(ln)
        remaining = [ln for ln in job.sov_lines if ln.kind != "contingency"]
    else:
        value = _q(job.contingency_amount)
        if existing:
            existing[0].scheduled_value = value
            for extra in existing[1:]:  # never keep more than one
                db.delete(extra)
            remaining = [ln for ln in job.sov_lines if ln.kind != "contingency"]
            remaining.append(existing[0])
        else:
            next_no = (max((ln.line_no for ln in job.sov_lines), default=0)) + 1
            row = SovLine(
                job_id=job.id,
                line_no=next_no,
                item_no=f"{next_no:02d}",
                description="Contingency",
                kind="contingency",
                scheduled_value=value,
            )
            db.add(row)
            remaining = list(job.sov_lines) + [row]
    db.flush()
    # Renumber 1..n in current order so a removal leaves no gap.
    for n, ln in enumerate(sorted(remaining, key=lambda x: x.line_no), start=1):
        ln.line_no = n
    db.flush()


def contract_summary(
    db: Session,
    job: Job,
    cost: Optional[Decimal] = None,
    billed: Optional[Decimal] = None,
) -> dict:
    """The contract roll-up for one job. `cost` (actual job-to-date cost) and
    `billed` (posted revenue carrying the job) may be passed in by a caller
    that already has them — the job detail hands over the profitability
    figures it computed, so a plain job fetch costs no extra query. When they
    are None the cost tree supplies them, so the Contract tab always agrees
    with the rest of the job page."""
    if cost is None or billed is None:
        from app.services.job_costing import job_cost_tree

        totals = job_cost_tree(db, job.id)["totals"]
        cost = Decimal(str(totals["actual"])) if cost is None else cost
        billed = Decimal(str(totals["act_revenue"])) if billed is None else billed
    cost = Decimal(str(cost))
    billed = Decimal(str(billed))

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
    if ctype in ("cost_plus", "gmp") and job.fee_amount is not None:
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
