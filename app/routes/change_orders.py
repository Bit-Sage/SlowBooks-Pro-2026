# ============================================================================
# Change orders (#554) — the document and its lifecycle.
#
# A change order is a potential change (PCO), an owner change order or a sub
# change order. It is bookkeeper-writable (like jobs); every effect on the
# contract, the SOV, the budget and the commitment happens on approve and
# reverses on void, in one transaction (app/services/change_orders.py).
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.cost_codes import CostCode
from app.models.jobs import Job
from app.models.purchase_orders import PurchaseOrder
from app.models.trade_packages import TradePackage
from app.schemas.change_orders import (
    ChangeOrderCreate,
    ChangeOrderOut,
    ChangeOrderUpdate,
    PromoteIn,
    SubOrderIn,
)
from app.services.change_orders import (
    apply_change_order,
    co_totals,
    recompute,
    reverse_change_order,
)
from app.services.job_costing import line_cost_type
from app.services.numbering import next_change_order_number
from app.services.pdf_service import generate_change_order_pdf
from app.services.request_utils import content_disposition
from app.services.settings_service import get_all_settings as get_settings

router = APIRouter(prefix="/api/change-orders", tags=["change-orders"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _get(db: Session, co_id: int) -> ChangeOrder:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status_code=404, detail="Change order not found")
    return co


def _co_out(db: Session, co: ChangeOrder) -> ChangeOrderOut:
    recompute(co, co.job)
    out = ChangeOrderOut.model_validate(co)
    for line_out, line in zip(out.lines, co.lines):
        line_out.cost_code_label = line.cost_code.label if line.cost_code else None
        line_out.trade_package_name = (
            line.trade_package.name if line.trade_package else None
        )
    totals = co_totals(co, co.job)
    out.cost_total = float(totals["cost_total"])
    out.markup_total = float(totals["markup_total"])
    out.sell_total = float(totals["sell_total"])
    out.by_cost_type = {
        t: {
            "cost": float(v["cost"]),
            "sell": float(v["sell"]),
            "markup": float(v["markup"]),
        }
        for t, v in totals["by_cost_type"].items()
    }
    return out


def _validate_line_refs(db: Session, line) -> str:
    """Validate a line's cost type / code / package, returning the effective
    cost type (required for a change-order line)."""
    if line.cost_code_id and not db.get(CostCode, line.cost_code_id):
        raise HTTPException(status_code=404, detail="Cost code not found")
    if line.trade_package_id and not db.get(TradePackage, line.trade_package_id):
        raise HTTPException(status_code=404, detail="Trade package not found")
    ctype = line_cost_type(db, line.cost_code_id, line.cost_type)
    if ctype is None:
        raise HTTPException(
            status_code=422, detail="A change order line needs a cost type"
        )
    return ctype


def _set_lines(db: Session, co: ChangeOrder, lines) -> None:
    db.query(ChangeOrderLine).filter(ChangeOrderLine.change_order_id == co.id).delete()
    db.flush()
    for i, line in enumerate(lines):
        ctype = _validate_line_refs(db, line)
        db.add(
            ChangeOrderLine(
                change_order_id=co.id,
                line_order=i,
                description=line.description,
                cost_type=ctype,
                cost_code_id=line.cost_code_id,
                trade_package_id=line.trade_package_id,
                quantity=line.quantity,
                unit=line.unit,
                unit_cost=line.unit_cost,
                markup_pct=line.markup_pct,
            )
        )
    db.flush()
    db.refresh(co)
    recompute(co, co.job)


def _assign_number(db: Session, co: ChangeOrder) -> None:
    """Assign the next number for the CO's kind with the race-retry pattern."""
    last_err = None
    for _ in range(10):
        co.number = next_change_order_number(db, co.kind)
        try:
            db.flush()
            return
        except IntegrityError as e:
            if "number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
    raise HTTPException(
        status_code=503, detail="Could not assign a change order number; please retry."
    ) from last_err


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ChangeOrderOut])
def list_change_orders(job_id: int = Query(...), db: Session = Depends(get_db)):
    cos = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.job_id == job_id)
        .order_by(ChangeOrder.id)
        .all()
    )
    return [_co_out(db, co) for co in cos]


@router.get("/{co_id}", response_model=ChangeOrderOut)
def get_change_order(co_id: int, db: Session = Depends(get_db)):
    return _co_out(db, _get(db, co_id))


@router.post("", response_model=ChangeOrderOut, status_code=201)
def create_change_order(data: ChangeOrderCreate, db: Session = Depends(get_db)):
    job = db.get(Job, data.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if data.funding == "allowance" and data.allowance_sov_line_id is None:
        raise HTTPException(
            status_code=422,
            detail="An allowance-funded change order needs an allowance line.",
        )
    if data.kind == "sub" and data.commitment_id is None:
        raise HTTPException(
            status_code=422, detail="A sub change order needs a commitment."
        )
    co = ChangeOrder(
        job_id=job.id,
        kind=data.kind,
        status="draft",
        title=data.title,
        description=data.description,
        date=data.date,
        funding=data.funding,
        allowance_sov_line_id=data.allowance_sov_line_id,
        markup_pct=data.markup_pct,
        commitment_id=data.commitment_id,
        owner_change_order_id=data.owner_change_order_id,
    )
    db.add(co)
    _assign_number(db, co)
    _set_lines(db, co, data.lines)
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.put("/{co_id}", response_model=ChangeOrderOut)
def update_change_order(
    co_id: int, data: ChangeOrderUpdate, db: Session = Depends(get_db)
):
    co = _get(db, co_id)
    if co.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_locked",
                "message": f"A {co.status} change order cannot be edited.",
            },
        )
    fields = data.model_dump(exclude_unset=True, exclude={"lines"})
    for key, val in fields.items():
        setattr(co, key, val)
    if co.funding == "allowance" and co.allowance_sov_line_id is None:
        raise HTTPException(
            status_code=422,
            detail="An allowance-funded change order needs an allowance line.",
        )
    if data.lines is not None:
        _set_lines(db, co, data.lines)
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.delete("/{co_id}")
def delete_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_draft",
                "message": "Only a draft change order can be deleted.",
            },
        )
    db.delete(co)
    db.commit()
    return {"deleted": True}


# ── Lifecycle ─────────────────────────────────────────────────────────────────


@router.post("/{co_id}/submit", response_model=ChangeOrderOut)
def submit_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_draft",
                "message": "Only a draft can be submitted.",
            },
        )
    co.status = "pending"
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/approve", response_model=ChangeOrderOut)
def approve_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.kind == "pco":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "promote_instead",
                "message": "A potential change order is promoted to an owner change order, not approved.",
            },
        )
    if co.status != "pending":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_pending",
                "message": f"A {co.status} change order cannot be approved.",
            },
        )
    apply_change_order(db, co)  # may 409 contingency/allowance_exceeded
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/reject", response_model=ChangeOrderOut)
def reject_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_pending",
                "message": f"A {co.status} change order cannot be rejected.",
            },
        )
    co.status = "rejected"
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/void", response_model=ChangeOrderOut)
def void_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_approved",
                "message": "Only an approved change order can be voided.",
            },
        )
    reverse_change_order(db, co)  # may 409 co_billed
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/promote", response_model=ChangeOrderOut)
def promote_change_order(
    co_id: int, data: PromoteIn | None = None, db: Session = Depends(get_db)
):
    """Promote a PCO to a new owner change order, copying its lines. The PCO
    becomes `promoted`; the new owner CO is returned."""
    pco = _get(db, co_id)
    if pco.kind != "pco":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_a_pco",
                "message": "Only a potential change order can be promoted.",
            },
        )
    if pco.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_promotable",
                "message": f"A {pco.status} potential change order cannot be promoted.",
            },
        )
    owner = ChangeOrder(
        job_id=pco.job_id,
        kind="owner",
        status="draft",
        title=pco.title,
        description=pco.description,
        date=pco.date,
        funding=pco.funding,
        allowance_sov_line_id=pco.allowance_sov_line_id,
        markup_pct=pco.markup_pct,
        source_pco_id=pco.id,
    )
    db.add(owner)
    _assign_number(db, owner)
    for i, line in enumerate(pco.lines):
        db.add(
            ChangeOrderLine(
                change_order_id=owner.id,
                line_order=i,
                description=line.description,
                cost_type=line.cost_type,
                cost_code_id=line.cost_code_id,
                trade_package_id=line.trade_package_id,
                quantity=line.quantity,
                unit=line.unit,
                unit_cost=line.unit_cost,
                markup_pct=line.markup_pct,
            )
        )
    pco.status = "promoted"
    db.commit()
    db.refresh(owner)
    return _co_out(db, owner)


@router.post("/{co_id}/sub-order", response_model=ChangeOrderOut)
def sub_order_from_owner(co_id: int, data: SubOrderIn, db: Session = Depends(get_db)):
    """From an owner change order, create a linked draft sub CO on a chosen
    commitment, copying the chosen lines (or the subcontract lines by
    default). A sub CO moves the commitment by cost; it carries no markup."""
    owner = _get(db, co_id)
    if owner.kind != "owner":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_an_owner_co",
                "message": "A sub change order is made from an owner change order.",
            },
        )
    po = db.get(PurchaseOrder, data.commitment_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Commitment not found")
    if data.line_ids is not None:
        chosen = [ln for ln in owner.lines if ln.id in set(data.line_ids)]
    else:
        chosen = [ln for ln in owner.lines if ln.cost_type == "subcontract"]
    if not chosen:
        raise HTTPException(
            status_code=422,
            detail="No lines to carry to the sub change order.",
        )
    sub = ChangeOrder(
        job_id=owner.job_id,
        kind="sub",
        status="draft",
        title=owner.title,
        description=owner.description,
        date=owner.date,
        funding="contract",
        commitment_id=po.id,
        owner_change_order_id=owner.id,
    )
    db.add(sub)
    _assign_number(db, sub)
    for i, line in enumerate(chosen):
        db.add(
            ChangeOrderLine(
                change_order_id=sub.id,
                line_order=i,
                description=line.description,
                cost_type=line.cost_type,
                cost_code_id=line.cost_code_id,
                trade_package_id=line.trade_package_id,
                quantity=line.quantity,
                unit=line.unit,
                unit_cost=line.unit_cost,
                markup_pct=line.markup_pct,
            )
        )
    db.commit()
    db.refresh(sub)
    return _co_out(db, sub)


@router.get("/{co_id}/pdf")
def change_order_pdf(co_id: int, db: Session = Depends(get_db)):
    """The owner CO / PCO document: title, description, lines with sell
    amounts per cost type, the markup shown as one line, and the total."""
    co = _get(db, co_id)
    recompute(co, co.job)
    company = get_settings(db)
    totals = co_totals(co, co.job)
    pdf_bytes = generate_change_order_pdf(co, totals, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(f"ChangeOrder_{co.number}.pdf")
        },
    )
