# ============================================================================
# Tax Report Routes — Schedule C generation, the entity returns (Form 1065 /
# 1120 / 1120-S, #488) and tax mappings
# Feature 19: Generate Schedule C from P&L data
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tax import TaxCategoryMapping
from app.models.accounts import Account
from app.schemas.tax import TaxMappingCreate, TaxMappingResponse
from app.services.settings_service import entity_info, get_all_settings
from app.services.tax_export import (
    TAX_FORMS,
    export_schedule_c_csv,
    get_schedule_c_data,
    get_tax_form_data,
)

router = APIRouter(prefix="/api/tax", tags=["tax"])


def _require_schedule_c(db: Session) -> None:
    """Schedule C is a sole-proprietor form (#488). A company that files
    something else (a partnership, a corporation, or a nonprofit on Form 990)
    is refused with 409 and the one sentence the page shows, so it never
    exports a form it does not file. An unset entity type is left exactly as
    before: an empty return_form is the signal to pass through."""
    info = entity_info(get_all_settings(db))
    if info["return_form"] and not info["schedule_c"]:
        # A nonprofit's return follows the company type, not the entity type,
        # so point it at the right control.
        where = "company type" if info["nonprofit"] else "entity type"
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_schedule_c",
                "message": (
                    f"This company is {info['label']}; it files "
                    f"{info['return_form']}, not Schedule C. Change the "
                    f"{where} under Settings → Company if that is wrong."
                ),
            },
        )


@router.get("/schedule-c")
def schedule_c_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    _require_schedule_c(db)
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date(date.today().year, 12, 31)
    return get_schedule_c_data(db, start_date, end_date)


@router.get("/schedule-c/csv")
def schedule_c_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    _require_schedule_c(db)
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date(date.today().year, 12, 31)
    data = get_schedule_c_data(db, start_date, end_date)
    csv_text = export_schedule_c_csv(data)
    from app.routes.csv import _csv_response

    return _csv_response(csv_text, f"schedule_c_{start_date}_{end_date}.csv")


def _entity_return(db: Session) -> str:
    """The return the company's entity type files, when this report can
    draw it (#488 step 2): Form 1065, 1120 or 1120-S, or Schedule C. An
    unset entity type and a nonprofit (Form 990) are refused with 409 and
    the sentence the page shows."""
    info = entity_info(get_all_settings(db))
    form = info["return_form"]
    if form in TAX_FORMS:
        return form
    if not form:
        message = (
            "Choose the company's entity type under Settings → Company "
            "first; the tax report follows the return it files."
        )
    else:
        message = (
            f"This company is {info['label']}; it files {form}, which this "
            "report does not draw."
        )
    raise HTTPException(
        status_code=409, detail={"code": "no_entity_return", "message": message}
    )


def _period(start_date, end_date):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date(date.today().year, 12, 31)
    return start_date, end_date


@router.get("/return")
def entity_return_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Page 1 of the return the entity type files (Form 1065, 1120 or
    1120-S; Schedule C for the entities that file it), drawn from the P&L
    the way the Schedule C report is."""
    form = _entity_return(db)
    start_date, end_date = _period(start_date, end_date)
    return get_tax_form_data(db, form, start_date, end_date)


@router.get("/return/csv")
def entity_return_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    form = _entity_return(db)
    start_date, end_date = _period(start_date, end_date)
    data = get_tax_form_data(db, form, start_date, end_date)
    csv_text = export_schedule_c_csv(data)
    from app.routes.csv import _csv_response

    stem = form.lower().replace(" ", "_").replace("-", "_")
    return _csv_response(csv_text, f"{stem}_{start_date}_{end_date}.csv")


@router.get("/mappings", response_model=list[TaxMappingResponse])
def list_mappings(db: Session = Depends(get_db)):
    mappings = db.query(TaxCategoryMapping).all()
    results = []
    for m in mappings:
        resp = TaxMappingResponse.model_validate(m)
        if m.account:
            resp.account_name = m.account.name
            resp.account_number = m.account.account_number
        results.append(resp)
    return results


@router.post("/mappings", response_model=TaxMappingResponse, status_code=201)
def create_mapping(data: TaxMappingCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(TaxCategoryMapping)
        .filter(TaxCategoryMapping.account_id == data.account_id)
        .first()
    )
    if existing:
        existing.tax_line = data.tax_line
    else:
        existing = TaxCategoryMapping(
            account_id=data.account_id, tax_line=data.tax_line
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    resp = TaxMappingResponse.model_validate(existing)
    acct = db.query(Account).filter(Account.id == existing.account_id).first()
    if acct:
        resp.account_name = acct.name
        resp.account_number = acct.account_number
    return resp
