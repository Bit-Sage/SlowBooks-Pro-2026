# ============================================================================
# Construction job reports (#556) — the standalone reports a contractor's books
# grow last: the WIP schedule with over/under billing, cost-to-complete, the
# change-order log, the buyout report and job cost by vendor.
#
# Pure functions over the figures the other services already compute — the
# contract summary (#552), the cost tree and budgets (M3), buyout (#553), change
# orders (#554) and pay applications (#555). Nothing here posts: the WIP
# over/under adjustment is the accountant's journal, named but never written.
# Money is Decimal, quantized to cents half-up through accounting._q.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q
from app.services.jobs_service import job_attribution

_ZERO = Decimal("0")
_ONE = Decimal("1")

# Jobs a WIP-style report leaves out unless asked: a lost bid never bills, and
# a closed job is off the board unless include_closed (or an explicit status).
_NEVER_DEFAULT = ("not_awarded",)
_CLOSED = ("closed",)


def select_jobs(
    db: Session,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> list[Job]:
    """The jobs a construction report runs over, customer then job name.

    `status` names one job status and wins; otherwise not-awarded jobs are
    always left out and closed ones too unless `include_closed`."""
    q = db.query(Job)
    if job_id is not None:
        q = q.filter(Job.id == job_id)
    if customer_id is not None:
        q = q.filter(Job.customer_id == customer_id)
    if status:
        q = q.filter(Job.status == status)
    else:
        excluded = list(_NEVER_DEFAULT)
        if not include_closed:
            excluded += list(_CLOSED)
        if excluded:
            q = q.filter(~Job.status.in_(excluded))
    jobs = q.all()
    return sorted(
        jobs,
        key=lambda j: (
            (j.customer.name.lower() if j.customer else ""),
            j.name.lower(),
        ),
    )


def _customer_name(job: Job) -> str:
    return job.customer.name if job.customer else ""


# ── 1. WIP schedule ─────────────────────────────────────────────────────────
#
# Cost-to-cost: percent complete is cost to date ÷ estimated total cost, earned
# revenue is the revised contract times that percent, and billings over earned
# are a liability (billings in excess of costs and estimated earnings), under
# are an asset (costs and estimated earnings in excess of billings). The
# adjusting entry an accountant would post is between those two accounts and
# the earned-revenue / cost accounts — this report computes it, it does not
# post it.

WIP_OVERBILLING_ACCOUNT = "Billings in Excess of Costs and Estimated Earnings"
WIP_UNDERBILLING_ACCOUNT = "Costs and Estimated Earnings in Excess of Billings"

WIP_COLUMNS = [
    {"key": "customer_name", "label": "Customer", "kind": "text"},
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "contract_original", "label": "Contract", "kind": "money"},
    {"key": "approved_change_orders", "label": "Changes", "kind": "money"},
    {"key": "revised_contract", "label": "Revised", "kind": "money"},
    {"key": "estimated_total_cost", "label": "Est. cost", "kind": "money"},
    {"key": "estimated_gross_profit", "label": "Est. GP", "kind": "money"},
    {"key": "margin_pct", "label": "Margin", "kind": "pct"},
    {"key": "cost_to_date", "label": "Cost to date", "kind": "money"},
    {"key": "percent_complete", "label": "% complete", "kind": "pct"},
    {"key": "earned_revenue", "label": "Earned", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
    {"key": "over_billing", "label": "Over billed", "kind": "money"},
    {"key": "under_billing", "label": "Under billed", "kind": "money"},
    {"key": "retainage_outstanding", "label": "Retainage", "kind": "money"},
    {"key": "cost_to_complete", "label": "Cost to complete", "kind": "money"},
    {"key": "projected_gross_profit", "label": "Proj. GP", "kind": "money"},
]

# The figures a totals row sums straight across.
_WIP_SUMMED = (
    "contract_original",
    "approved_change_orders",
    "revised_contract",
    "estimated_total_cost",
    "estimated_gross_profit",
    "cost_to_date",
    "earned_revenue",
    "billed_to_date",
    "over_billing",
    "under_billing",
    "retainage_outstanding",
    "cost_to_complete",
    "projected_gross_profit",
)


def _blank_wip_row(job: Job) -> dict:
    row = {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "status": job.status,
        "has_contract": False,
        "billed_basis": None,
    }
    for key in _WIP_SUMMED:
        row[key] = None
    row["margin_pct"] = None
    row["percent_complete"] = None
    return row


def wip_row(db: Session, job: Job, as_of: date) -> dict:
    """One WIP line for a job. A job with no contract amount is listed with
    blank figures (the schedule still shows it)."""
    from app.services.contracts import contract_summary
    from app.services.job_costing import job_cost_tree
    from app.services.pay_applications import has_any_application

    if job.contract_amount is None:
        return _blank_wip_row(job)

    totals = job_cost_tree(db, job.id, end_date=as_of)["totals"]
    cost_to_date = _q(totals["actual"])
    est_total_cost = _q(totals["revised"])  # budget original + changes (cost side)
    posted_revenue = _q(totals["act_revenue"])

    summary = contract_summary(db, job, cost=cost_to_date, billed=posted_revenue)
    revised = _q(summary["contract_amount"])
    original = (
        _q(summary["original_contract_amount"])
        if summary["original_contract_amount"] is not None
        else revised
    )
    approved_cos = _q(summary["changes"])
    billed = _q(summary["billed_to_date"])
    retainage_out = _q(summary["retainage"]["outstanding"])

    if est_total_cost > _ZERO:
        pct = cost_to_date / est_total_cost
        if pct > _ONE:
            pct = _ONE  # capped at 100% — a cost overrun does not earn past the sum
    else:
        pct = _ZERO
    earned = _q(revised * pct)
    over = _q(billed - earned) if billed > earned else _ZERO
    under = _q(earned - billed) if earned > billed else _ZERO
    cost_to_complete = _q(est_total_cost - cost_to_date)
    projected_final_cost = _q(cost_to_date + cost_to_complete)
    projected_gp = _q(revised - projected_final_cost)
    estimated_gp = _q(revised - est_total_cost)
    margin = _q(estimated_gp / revised * 100) if revised else None

    basis = "pay_applications" if has_any_application(db, job.id) else "posted_revenue"
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "status": job.status,
        "has_contract": True,
        "billed_basis": basis,
        "contract_original": float(original),
        "approved_change_orders": float(approved_cos),
        "revised_contract": float(revised),
        "estimated_total_cost": float(est_total_cost),
        "estimated_gross_profit": float(estimated_gp),
        "margin_pct": float(margin) if margin is not None else None,
        "cost_to_date": float(cost_to_date),
        "percent_complete": float(_q(pct * 100)),
        "earned_revenue": float(earned),
        "billed_to_date": float(billed),
        "over_billing": float(over),
        "under_billing": float(under),
        "retainage_outstanding": float(retainage_out),
        "cost_to_complete": float(cost_to_complete),
        "projected_gross_profit": float(projected_gp),
    }


def wip_schedule(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """The work-in-progress schedule: one row per job, cost-to-cost, with over
    and under billing. Actuals are as of `as_of`; budgets and billing are
    job-to-date."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [wip_row(db, job, as_of) for job in jobs]

    totals = {key: _ZERO for key in _WIP_SUMMED}
    for r in rows:
        for key in _WIP_SUMMED:
            if r[key] is not None:
                totals[key] += Decimal(str(r[key]))
    revised = totals["revised_contract"]
    out_totals = {key: float(_q(v)) for key, v in totals.items()}
    out_totals["margin_pct"] = (
        float(_q(totals["estimated_gross_profit"] / revised * 100)) if revised else None
    )
    return {
        "as_of": as_of.isoformat(),
        "method": "cost_to_cost",
        "rows": rows,
        "totals": out_totals,
        "columns": WIP_COLUMNS,
        "adjustment_accounts": {
            "over_billing": WIP_OVERBILLING_ACCOUNT,
            "under_billing": WIP_UNDERBILLING_ACCOUNT,
        },
    }


# ── 2. Cost to complete ───────────────────────────────────────────────────

CTC_COLUMNS = [
    {"key": "label", "label": "Cost code", "kind": "text"},
    {"key": "budget_revised", "label": "Budget", "kind": "money"},
    {"key": "committed", "label": "Committed", "kind": "money"},
    {"key": "actual", "label": "Actual", "kind": "money"},
    {"key": "cost_to_complete", "label": "Cost to complete", "kind": "money"},
    {"key": "projected_final", "label": "Projected final", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "pct_complete", "label": "% complete", "kind": "pct"},
]


def _ctc_figures(raw: dict) -> dict:
    """Derive the cost-to-complete columns from the aggregated raw figures of a
    node (budget original/changes, committed remaining, actual)."""
    revised = _q(raw["orig"] + raw["chg"])
    committed = _q(raw["committed"])
    actual = _q(raw["actual"])
    remaining_to_budget = revised - actual - committed
    if remaining_to_budget < _ZERO:
        remaining_to_budget = _ZERO
    cost_to_complete = _q(remaining_to_budget + committed)
    projected_final = _q(actual + cost_to_complete)
    variance = _q(revised - projected_final)
    pct = _q(actual / revised * 100) if revised else None
    return {
        "budget_revised": float(revised),
        "committed": float(committed),
        "actual": float(actual),
        "cost_to_complete": float(cost_to_complete),
        "projected_final": float(projected_final),
        "variance": float(variance),
        "pct_complete": float(pct) if pct is not None else None,
    }


def _blank_raw() -> dict:
    return {"orig": _ZERO, "chg": _ZERO, "committed": _ZERO, "actual": _ZERO}


def _ctc_job(db: Session, job: Job, as_of: date) -> dict:
    """Cost-to-complete for one job: the cost-code tree (division › section)
    with budget / committed / actual rolled up, plus an Uncoded row and totals.
    """
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
    from app.services.job_costing import code_tree

    codes = db.query(CostCode).all()

    raw: dict = {}  # cost_code_id (or None) -> raw figures

    def bucket(code_id):
        return raw.setdefault(code_id, _blank_raw())

    for b in db.query(JobBudget).filter(JobBudget.job_id == job.id):
        d = bucket(b.cost_code_id)
        if b.source == "change":
            d["chg"] += Decimal(str(b.amount))
        else:
            d["orig"] += Decimal(str(b.amount))

    # actual cost (COGS + expense; income is not a cost) as of the date
    cost_types = (AccountType.COGS, AccountType.EXPENSE)
    q = (
        db.query(
            TransactionLine.cost_code_id,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(job_attribution() == job.id, Account.account_type.in_(cost_types))
    )
    if as_of is not None:
        q = q.filter(Transaction.date <= as_of)
    for code_id, dr, cr in q.group_by(TransactionLine.cost_code_id).all():
        bucket(code_id)["actual"] += Decimal(str(dr)) - Decimal(str(cr))

    # committed: the remaining (unbilled) part of each open commitment line
    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    cq = (
        db.query(
            PurchaseOrderLine.cost_code_id,
            sqlfunc.coalesce(
                sqlfunc.sum(PurchaseOrderLine.amount - PurchaseOrderLine.billed_amount),
                0,
            ),
        )
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
        .filter(
            line_job == job.id,
            PurchaseOrder.status.in_(
                (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
            ),
        )
    )
    for code_id, amt in cq.group_by(PurchaseOrderLine.cost_code_id).all():
        bucket(code_id)["committed"] += Decimal(str(amt))

    grand = _blank_raw()

    def node_for(n):
        c = n["code"]
        children = [node_for(ch) for ch in n["children"]]
        own = raw.get(c.id, _blank_raw())
        agg = {k: own[k] + sum((ch["_raw"][k] for ch in children), _ZERO) for k in own}
        if not any(agg.values()):
            return None
        kept = [ch for ch in children if ch is not None]
        return {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "label": c.label,
            "level": c.level,
            "figures": _ctc_figures(agg),
            "children": kept,
            "_raw": agg,
        }

    tree_nodes = []
    for n in code_tree(codes):
        built = node_for(n)
        if built is not None:
            tree_nodes.append(built)

    def accrue(node):
        for k in grand:
            grand[k] += node["_raw"][k]

    def strip(node):
        for ch in node.get("children", []):
            strip(ch)
        node.pop("_raw", None)

    for node in tree_nodes:
        accrue(node)

    uncoded_raw = raw.get(None, _blank_raw())
    for k in grand:
        grand[k] += uncoded_raw[k]

    for node in tree_nodes:
        strip(node)

    uncoded = _ctc_figures(uncoded_raw) if any(uncoded_raw.values()) else None
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "tree": tree_nodes,
        "uncoded": uncoded,
        "totals": _ctc_figures(grand),
    }


def cost_to_complete(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Cost to complete per job, per cost code, with grand totals."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [_ctc_job(db, job, as_of) for job in jobs]

    grand = _blank_raw()
    for job_data in rows:
        t = job_data["totals"]
        grand["actual"] += Decimal(str(t["actual"]))
        grand["committed"] += Decimal(str(t["committed"]))
        # back out budget revised into orig (changes already folded in)
        grand["orig"] += Decimal(str(t["budget_revised"]))
    return {
        "as_of": as_of.isoformat(),
        "jobs": rows,
        "totals": _ctc_figures(grand),
        "columns": CTC_COLUMNS,
    }


# ── 3. Change-order log ───────────────────────────────────────────────────

CO_COLUMNS = [
    {"key": "number", "label": "Number", "kind": "text"},
    {"key": "kind", "label": "Kind", "kind": "text"},
    {"key": "status", "label": "Status", "kind": "text"},
    {"key": "title", "label": "Title", "kind": "text"},
    {"key": "date", "label": "Date", "kind": "text"},
    {"key": "funding", "label": "Funding", "kind": "text"},
    {"key": "cost", "label": "Cost", "kind": "money"},
    {"key": "markup", "label": "Markup", "kind": "money"},
    {"key": "sell", "label": "Sell", "kind": "money"},
    {"key": "running_approved", "label": "Approved to date", "kind": "money"},
]


def _co_job(db: Session, job: Job) -> dict:
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_totals
    from app.services.contracts import contract_summary

    cos = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.job_id == job.id)
        .order_by(ChangeOrder.number)
        .all()
    )
    counts: dict[str, int] = {}
    pending_total = _ZERO
    running = _ZERO
    out_rows = []
    for co in cos:
        counts[co.status] = counts.get(co.status, 0) + 1
        figs = co_totals(co, job)
        cost = figs["cost_total"]
        sell = figs["sell_total"]
        markup = figs["markup_total"]
        # The running approved total follows the contract: an approved,
        # contract-funded owner CO adds its sell. Others leave it where it is.
        if co.status == "approved" and co.kind == "owner" and co.funding == "contract":
            running = _q(running + sell)
        if co.status == "pending" and co.kind == "owner":
            pending_total += sell
        commitment = None
        if co.kind == "sub" and co.commitment is not None:
            commitment = {
                "id": co.commitment.id,
                "number": co.commitment.po_number,
                "vendor_name": (
                    co.commitment.vendor.name if co.commitment.vendor else ""
                ),
            }
        out_rows.append(
            {
                "id": co.id,
                "number": co.number,
                "kind": co.kind,
                "status": co.status,
                "title": co.title or "",
                "date": co.date.isoformat() if co.date else None,
                "funding": co.funding,
                "cost": float(cost),
                "markup": float(markup),
                "sell": float(sell),
                "approved_at": co.approved_at.isoformat() if co.approved_at else None,
                "voided_at": co.voided_at.isoformat() if co.voided_at else None,
                "source_pco_id": co.source_pco_id,
                "commitment": commitment,
                "running_approved": float(running),
            }
        )

    summary = contract_summary(db, job)
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "contract": {
            "original": summary["original_contract_amount"],
            "approved_changes": summary["changes"],
            "revised": summary["contract_amount"],
        },
        "pending_total": float(_q(pending_total)),
        "counts": counts,
        "rows": out_rows,
    }


def change_order_log(
    db: Session,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Every change order per job, with the running approved total and the
    contract it builds (original + approved changes = revised)."""
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [_co_job(db, job) for job in jobs]
    counts: dict[str, int] = {}
    pending_total = _ZERO
    for job_data in rows:
        for st, n in job_data["counts"].items():
            counts[st] = counts.get(st, 0) + n
        pending_total += Decimal(str(job_data["pending_total"]))
    return {
        "jobs": rows,
        "totals": {"counts": counts, "pending_total": float(_q(pending_total))},
        "columns": CO_COLUMNS,
    }


# ── 4. Buyout report ──────────────────────────────────────────────────────

BUYOUT_COLUMNS = [
    {"key": "package_name", "label": "Package", "kind": "text"},
    {"key": "bid_cost", "label": "Bid", "kind": "money"},
    {"key": "bought", "label": "Bought", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "committed_remaining", "label": "Remaining", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
]

_BUYOUT_SUMMED = (
    "bid_cost",
    "bought",
    "variance",
    "committed_remaining",
    "billed_to_date",
)


def buyout_report(
    db: Session,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """The job_buyout rows across jobs, totals per job and grand (#553 is the
    per-job seam; this foots it across the company)."""
    from app.services.buyout import job_buyout

    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = []
    grand = {k: _ZERO for k in _BUYOUT_SUMMED}
    for job in jobs:
        data = job_buyout(db, job)
        for k in _BUYOUT_SUMMED:
            grand[k] += Decimal(str(data["totals"][k]))
        rows.append(
            {
                "job_id": job.id,
                "job_name": job.name,
                "customer_id": job.customer_id,
                "customer_name": _customer_name(job),
                "bid_estimate_id": data["bid_estimate_id"],
                "rows": data["rows"],
                "totals": data["totals"],
            }
        )
    return {
        "jobs": rows,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
        "columns": BUYOUT_COLUMNS,
    }


# ── 5. Job cost by vendor ─────────────────────────────────────────────────

VENDOR_COLUMNS = [
    {"key": "vendor_name", "label": "Vendor", "kind": "text"},
    {"key": "cost", "label": "Cost", "kind": "money"},
    {"key": "bills_count", "label": "Bills", "kind": "int"},
    {"key": "paid_to_date", "label": "Paid", "kind": "money"},
    {"key": "retainage_outstanding", "label": "Retainage held", "kind": "money"},
    {"key": "open_ap", "label": "Open A/P", "kind": "money"},
]

_VENDOR_SUMMED = ("cost", "paid_to_date", "retainage_outstanding", "open_ap")

_NO_VENDOR = "No vendor"


def _vendor_bill_figures(db: Session, job_id: int, vendor_id: int) -> dict:
    """For one vendor on one job: the count of its bills carrying the job, the
    bill payments allocated to them (non-void), the retainage held but not
    released, and the open A/P balance. A bill carries the job on its header or
    on any of its lines."""
    from sqlalchemy import or_

    from app.models.bills import (
        Bill,
        BillLine,
        BillPayment,
        BillPaymentAllocation,
    )

    on_line = db.query(BillLine.bill_id).filter(BillLine.job_id == job_id)
    bills = (
        db.query(Bill)
        .filter(
            Bill.vendor_id == vendor_id,
            or_(Bill.job_id == job_id, Bill.id.in_(on_line)),
        )
        .all()
    )
    bill_ids = [b.id for b in bills]
    retainage = sum(
        (
            Decimal(str(b.retainage_held or 0))
            - Decimal(str(b.retainage_released or 0))
            for b in bills
        ),
        _ZERO,
    )
    open_ap = sum((Decimal(str(b.balance_due or 0)) for b in bills), _ZERO)
    paid = _ZERO
    if bill_ids:
        paid = (
            db.query(sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0))
            .select_from(BillPaymentAllocation)
            .join(
                BillPayment,
                BillPaymentAllocation.bill_payment_id == BillPayment.id,
            )
            .filter(
                BillPaymentAllocation.bill_id.in_(bill_ids),
                BillPayment.is_voided.is_(False),
            )
            .scalar()
            or 0
        )
    return {
        "bills_count": len(bills),
        "paid_to_date": _q(paid),
        "retainage_outstanding": _q(retainage),
        "open_ap": _q(open_ap),
    }


def _vendor_job(
    db: Session,
    job: Job,
    start_date: Optional[date],
    end_date: Optional[date],
) -> dict:
    from app.models.contacts import Vendor

    cost_types = (AccountType.COGS, AccountType.EXPENSE)
    q = (
        db.query(
            Transaction.vendor_id,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(job_attribution() == job.id, Account.account_type.in_(cost_types))
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    by_vendor = q.group_by(Transaction.vendor_id).all()

    vendor_ids = [v for v, _dr, _cr in by_vendor if v is not None]
    names = {
        v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()
    }

    rows = []
    for vendor_id, dr, cr in by_vendor:
        cost = Decimal(str(dr)) - Decimal(str(cr))
        if vendor_id is None:
            rows.append(
                {
                    "vendor_id": None,
                    "vendor_name": _NO_VENDOR,
                    "cost": float(_q(cost)),
                    "bills_count": 0,
                    "paid_to_date": 0.0,
                    "retainage_outstanding": 0.0,
                    "open_ap": 0.0,
                }
            )
            continue
        bf = _vendor_bill_figures(db, job.id, vendor_id)
        rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": names.get(vendor_id, ""),
                "cost": float(_q(cost)),
                "bills_count": bf["bills_count"],
                "paid_to_date": float(bf["paid_to_date"]),
                "retainage_outstanding": float(bf["retainage_outstanding"]),
                "open_ap": float(bf["open_ap"]),
            }
        )
    # named vendors first by name, the No vendor bucket last
    rows.sort(key=lambda r: (r["vendor_id"] is None, (r["vendor_name"] or "").lower()))

    totals = {k: _ZERO for k in _VENDOR_SUMMED}
    for r in rows:
        for k in _VENDOR_SUMMED:
            totals[k] += Decimal(str(r[k]))
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "rows": rows,
        "totals": {k: float(_q(v)) for k, v in totals.items()},
    }


def job_cost_by_vendor(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Posted job cost grouped job → vendor (the transaction header's vendor; a
    No vendor bucket holds the rest), with the vendor's bills count, paid to
    date, retainage held outstanding and open A/P on the job. Cost is
    period-filterable by transaction date; the bill figures are job-to-date."""
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [_vendor_job(db, job, start_date, end_date) for job in jobs]
    grand = {k: _ZERO for k in _VENDOR_SUMMED}
    for job_data in rows:
        for k in _VENDOR_SUMMED:
            grand[k] += Decimal(str(job_data["totals"][k]))
    return {
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "jobs": rows,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
        "columns": VENDOR_COLUMNS,
    }
