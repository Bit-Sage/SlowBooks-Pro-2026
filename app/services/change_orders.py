# ============================================================================
# Change orders (#554) — the math and the effects, in one place.
#
# A change order prices material and labor separately, each line carrying its
# own markup, and every effect on the contract, the SOV, the budget and the
# commitment happens on approve (apply_change_order) and reverses on void
# (reverse_change_order), each in one transaction the route owns.
#
# The math is pure (compute_line, co_totals): Decimal, cents half-up. The
# effects read the job and write JobBudget/SovLine/PurchaseOrderLine rows.
# Later: #555 progress billing bills the revised SOV and would touch a
# change_order-kind line's billing (the co_billed guard reserves that case).
# ============================================================================

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.job_costing import JobBudget
from app.models.jobs import Job, SovLine
from app.models.purchase_orders import PurchaseOrder
from app.services.accounting import _q

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Pure math ────────────────────────────────────────────────────────────


def line_cost(quantity, unit_cost) -> Decimal:
    """A line's cost: quantity × unit cost, to the cent."""
    return _q(Decimal(str(quantity or 0)) * Decimal(str(unit_cost or 0)))


def effective_markup(line_markup, co_markup, job_markup, funding: str) -> Decimal:
    """The percent that applies to a line: the line's own, else the CO's, else
    the job's default, else zero — but forced to zero when an allowance pays
    for the work (an allowance carries no markup)."""
    if funding == "allowance":
        return _ZERO
    for pct in (line_markup, co_markup, job_markup):
        if pct is not None:
            return Decimal(str(pct))
    return _ZERO


def line_sell(cost: Decimal, markup: Decimal, kind: str) -> Decimal:
    """A line's sell amount. An owner CO or a PCO carries the markup; a sub CO
    moves a commitment by cost, so it carries none."""
    if kind == "sub":
        return _q(cost)
    return _q(cost * (Decimal("1") + markup / _HUNDRED))


def compute_line(line: ChangeOrderLine, co: ChangeOrder, job: Job) -> None:
    """Stamp a line's server-computed amount (cost) and sell_amount in place."""
    cost = line_cost(line.quantity, line.unit_cost)
    markup = effective_markup(
        line.markup_pct, co.markup_pct, job.co_markup_pct, co.funding
    )
    line.amount = cost
    line.sell_amount = line_sell(cost, markup, co.kind)


def recompute(co: ChangeOrder, job: Job) -> None:
    """Recompute every line of a CO (call before reading its totals or saving)."""
    for line in co.lines:
        compute_line(line, co, job)


def co_totals(co: ChangeOrder, job: Job) -> dict:
    """Cost, markup and sell totals for a CO, plus the same per cost type."""
    recompute(co, job)
    cost_total = sum((_q(line.amount) for line in co.lines), _ZERO)
    sell_total = sum((_q(line.sell_amount) for line in co.lines), _ZERO)
    by_type: dict[str, dict] = {}
    for line in co.lines:
        bucket = by_type.setdefault(line.cost_type, {"cost": _ZERO, "sell": _ZERO})
        bucket["cost"] += _q(line.amount)
        bucket["sell"] += _q(line.sell_amount)
    return {
        "cost_total": _q(cost_total),
        "sell_total": _q(sell_total),
        "markup_total": _q(sell_total - cost_total),
        "by_cost_type": {
            t: {
                "cost": _q(v["cost"]),
                "sell": _q(v["sell"]),
                "markup": _q(v["sell"] - v["cost"]),
            }
            for t, v in by_type.items()
        },
    }


def _budget_groups(co: ChangeOrder) -> dict:
    """CO lines grouped by (cost_code_id, cost_type) -> (cost, sell)."""
    groups: dict[tuple, list] = {}
    for line in co.lines:
        key = (line.cost_code_id, line.cost_type)
        bucket = groups.setdefault(key, [_ZERO, _ZERO])
        bucket[0] += _q(line.amount)
        bucket[1] += _q(line.sell_amount)
    return groups


# ── Usage roll-ups (read by the contract summary and the exceed guards) ────


def _approved_owner_cos(db: Session, job_id: int, funding: str) -> list[ChangeOrder]:
    return (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job_id,
            ChangeOrder.kind == "owner",
            ChangeOrder.status == "approved",
            ChangeOrder.funding == funding,
        )
        .all()
    )


def contingency_used(db: Session, job: Job) -> Decimal:
    """Σ sell_total of approved contingency-funded owner COs."""
    total = _ZERO
    for co in _approved_owner_cos(db, job.id, "contingency"):
        total += co_totals(co, job)["sell_total"]
    return _q(total)


def allowance_used(db: Session, job: Job, sov_line_id: int) -> Decimal:
    """Σ cost_total of approved allowance-funded owner COs on one allowance
    SOV line (an allowance pays for work at cost — no markup)."""
    total = _ZERO
    for co in _approved_owner_cos(db, job.id, "allowance"):
        if co.allowance_sov_line_id == sov_line_id:
            total += co_totals(co, job)["cost_total"]
    return _q(total)


def allowances_summary(db: Session, job: Job) -> list[dict]:
    """Per allowance SOV line: its scheduled value, used and remaining."""
    out = []
    for line in job.sov_lines:
        if line.kind != "allowance":
            continue
        value = _q(line.scheduled_value)
        used = allowance_used(db, job, line.id)
        out.append(
            {
                "sov_line_id": line.id,
                "description": line.description,
                "scheduled_value": float(value),
                "used": float(used),
                "remaining": float(_q(value - used)),
            }
        )
    return out


def change_orders_summary(db: Session, job: Job) -> dict:
    """Approved/pending counts and the approved contract-funded total."""
    cos = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.job_id == job.id, ChangeOrder.kind == "owner")
        .all()
    )
    approved = [c for c in cos if c.status == "approved"]
    pending = [c for c in cos if c.status == "pending"]
    approved_total = _ZERO
    for co in approved:
        if co.funding == "contract":
            approved_total += co_totals(co, job)["sell_total"]
    return {
        "approved_count": len(approved),
        "pending_count": len(pending),
        "approved_total": float(_q(approved_total)),
    }


# ── Budget rows (source="change", locked) ──────────────────────────────────


def _write_change_budgets(
    db: Session, job_id: int, co: ChangeOrder, revenue_mode: str
) -> None:
    """Add this CO's cost/revenue into the job's source="change" budget rows,
    one per (cost_code_id, cost_type). revenue_mode: "sell" (contract /
    contingency), "cost" (allowance — revenue equals cost, no markup) or
    "zero" (a sub CO — a commitment carries no revenue). Bypasses
    _refuse_locked_budget by design: a change order is the one thing that may
    move a locked budget."""
    for (code_id, ctype), (cost, sell) in _budget_groups(co).items():
        if revenue_mode == "sell":
            rev = sell
        elif revenue_mode == "cost":
            rev = cost
        else:
            rev = _ZERO
        row = _find_change_budget(db, job_id, code_id, ctype)
        if row is None:
            db.add(
                JobBudget(
                    job_id=job_id,
                    cost_code_id=code_id,
                    cost_type=ctype,
                    amount=cost,
                    revenue_amount=rev,
                    source="change",
                    locked=True,
                )
            )
        else:
            row.amount = _q(Decimal(str(row.amount)) + cost)
            row.revenue_amount = _q(Decimal(str(row.revenue_amount)) + rev)
    db.flush()


def _remove_change_budgets(
    db: Session, job_id: int, co: ChangeOrder, revenue_mode: str
) -> None:
    """Reverse _write_change_budgets: subtract this CO's cost/revenue from the
    change rows, deleting a row that reaches zero."""
    for (code_id, ctype), (cost, sell) in _budget_groups(co).items():
        rev = (
            sell
            if revenue_mode == "sell"
            else (cost if revenue_mode == "cost" else _ZERO)
        )
        row = _find_change_budget(db, job_id, code_id, ctype)
        if row is None:
            continue
        row.amount = _q(Decimal(str(row.amount)) - cost)
        row.revenue_amount = _q(Decimal(str(row.revenue_amount)) - rev)
        if row.amount <= _ZERO and row.revenue_amount <= _ZERO:
            db.delete(row)
    db.flush()


def _find_change_budget(db: Session, job_id: int, code_id, ctype):
    q = db.query(JobBudget).filter(
        JobBudget.job_id == job_id,
        JobBudget.source == "change",
    )
    q = (
        q.filter(JobBudget.cost_code_id == code_id)
        if code_id is not None
        else q.filter(JobBudget.cost_code_id.is_(None))
    )
    q = (
        q.filter(JobBudget.cost_type == ctype)
        if ctype is not None
        else q.filter(JobBudget.cost_type.is_(None))
    )
    return q.first()


def _revenue_mode(co: ChangeOrder) -> str:
    if co.kind == "sub":
        return "zero"
    if co.funding == "allowance":
        return "cost"
    return "sell"


# ── Commitment append (sub CO) ──────────────────────────────────────────────


def remove_commitment_lines(db: Session, po: PurchaseOrder, co: ChangeOrder) -> None:
    """Reverse append_commitment_lines on void. A line already billed against
    cannot be pulled (409 co_billed)."""
    co_amounts = [_q(line.amount) for line in co.lines]
    # Match this CO's appended lines by their unbilled amount, newest first.
    appended = [ln for ln in po.lines if _q(ln.billed_amount) == _ZERO]
    for amt in co_amounts:
        match = next((ln for ln in appended if _q(ln.amount) == amt), None)
        if match is None:
            # A line we would remove has been billed — refuse the void.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "co_billed",
                    "message": "A commitment line this change order added has been billed; it cannot be voided.",
                },
            )
        appended.remove(match)
        db.delete(match)
    db.flush()
    db.refresh(po)
    from app.routes.purchase_orders import _retotal_commitment

    _retotal_commitment(po)
    db.flush()


# ── Apply / reverse ─────────────────────────────────────────────────────────


def _next_sov_line_no(job: Job) -> int:
    return (max((r.line_no for r in job.sov_lines), default=0)) + 1


def apply_change_order(db: Session, co: ChangeOrder) -> None:
    """Every effect of approving a change order, in the route's transaction.
    Raises 409 contingency_exceeded / allowance_exceeded when a draw would
    pass its limit."""
    job = co.job
    recompute(co, job)
    totals = co_totals(co, job)
    sell_total = totals["sell_total"]

    if co.kind == "owner":
        if co.funding == "contract":
            base = Decimal(str(job.contract_amount or 0))
            job.contract_amount = _q(base + sell_total)
            line = SovLine(
                job_id=job.id,
                line_no=_next_sov_line_no(job),
                item_no=None,
                description=f"{co.number} {co.title or ''}".strip(),
                kind="change_order",
                scheduled_value=sell_total,
                change_order_id=co.id,
            )
            db.add(line)
            _write_change_budgets(db, job.id, co, "sell")
        elif co.funding == "contingency":
            limit = job.contingency_amount
            if limit is not None:
                already = contingency_used(db, job)  # excludes this not-yet-approved CO
                if _q(already + sell_total) > _q(limit):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "contingency_exceeded",
                            "message": "This change order would draw more than the contract's contingency.",
                        },
                    )
            _write_change_budgets(db, job.id, co, "sell")
        elif co.funding == "allowance":
            if co.allowance_sov_line_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="An allowance-funded change order needs an allowance line.",
                )
            sov_line = db.get(SovLine, co.allowance_sov_line_id)
            if sov_line is not None:
                already = allowance_used(db, job, sov_line.id)
                if _q(already + totals["cost_total"]) > _q(sov_line.scheduled_value):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "allowance_exceeded",
                            "message": "This change order would spend more than the allowance holds.",
                        },
                    )
            _write_change_budgets(db, job.id, co, "cost")
    elif co.kind == "sub":
        if co.commitment_id is None:
            raise HTTPException(
                status_code=422, detail="A sub change order needs a commitment."
            )
        po = db.get(PurchaseOrder, co.commitment_id)
        if po is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        from app.routes.purchase_orders import append_commitment_lines

        append_commitment_lines(db, po, co.lines)
        _write_change_budgets(db, job.id, co, "zero")

    co.status = "approved"
    co.approved_at = datetime.now(timezone.utc)
    db.flush()


def reverse_change_order(db: Session, co: ChangeOrder) -> None:
    """Undo apply_change_order, in the route's transaction."""
    job = co.job
    recompute(co, job)
    totals = co_totals(co, job)
    sell_total = totals["sell_total"]

    if co.kind == "owner":
        if co.funding == "contract":
            base = Decimal(str(job.contract_amount or 0))
            job.contract_amount = _q(base - sell_total)
            db.query(SovLine).filter(
                SovLine.job_id == job.id, SovLine.change_order_id == co.id
            ).delete()
            _remove_change_budgets(db, job.id, co, "sell")
        elif co.funding == "contingency":
            _remove_change_budgets(db, job.id, co, "sell")
        elif co.funding == "allowance":
            _remove_change_budgets(db, job.id, co, "cost")
    elif co.kind == "sub" and co.commitment_id is not None:
        po = db.get(PurchaseOrder, co.commitment_id)
        if po is not None:
            remove_commitment_lines(db, po, co)  # may 409 co_billed
        _remove_change_budgets(db, job.id, co, "zero")

    co.status = "void"
    co.voided_at = datetime.now(timezone.utc)
    db.flush()
