# ============================================================================
# Company file encryption — status and the switch (issue #290).
#
# GET  /api/company-file/encryption   what Settings shows an administrator
# POST /api/company-file/encryption   turn it on or off, in place
#
# Both are an administrator's. The switch also asks for the company's name,
# typed, the way a restore asks before it replaces the books: a change that
# rewrites every page of the company file is not one click.
#
# The key, in this release, is the operator's COMPANY_FILE_ENCRYPTION_SECRET
# (Server Edition, set the way PAYROLL_ENCRYPTION_SECRET is). A desktop
# without one is told so; the desktop's own key management comes next.
# PostgreSQL deployments are told that encryption at rest is the host's.
# ============================================================================

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._roles import require_admin
from app.schemas.common import StrictModel
from app.services import company_file
from app.services.backup_service import _same_name, current_company_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/company-file", tags=["company-file"])

NO_KEY_SOURCE_MESSAGE = (
    "No key is set for this company file: set COMPANY_FILE_ENCRYPTION_SECRET "
    "where SlowBooks Pro starts and start it again, then turn encryption on."
)
NAME_MISMATCH_MESSAGE = (
    "Type the company's name exactly as it appears in Settings to confirm."
)


class EncryptionChange(StrictModel):
    enabled: bool
    company_name: str


@router.get("/encryption")
def encryption_status(request: Request):
    require_admin(request)
    return company_file.status()


@router.post("/encryption")
def change_encryption(
    data: EncryptionChange, request: Request, db: Session = Depends(get_db)
):
    require_admin(request)
    state = company_file.status()
    if state["engine"] == "postgresql":
        raise HTTPException(status_code=409, detail=company_file.POSTGRESQL_MESSAGE)
    if not state["supported"]:
        raise HTTPException(status_code=409, detail=company_file.UNAVAILABLE_MESSAGE)
    if not _same_name(data.company_name, current_company_name(db)):
        raise HTTPException(status_code=400, detail=NAME_MISMATCH_MESSAGE)
    if data.enabled == state["encrypted"]:
        return {**state, "changed": False}
    key = company_file.resolve_key()
    if key is None:
        raise HTTPException(status_code=409, detail=NO_KEY_SOURCE_MESSAGE)

    # The file is rewritten in place. This request's session and the pool's
    # idle connections are let go first: on Windows an open handle refuses
    # the replace, and every connection after this one must key itself (or
    # not) by what is now on disk.
    path = company_file.active_path()
    db.close()
    import app.database as db_module

    db_module.engine.dispose()
    try:
        company_file.convert(path, key, encrypt=data.enabled)
    except company_file.CompanyFileLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        # Never the exception text: it names paths (py/stack-trace-exposure).
        logger.exception("company file conversion failed")
        raise HTTPException(
            status_code=500,
            detail="The company file could not be converted; the books are "
            "as they were. Check the log.",
        )
    finally:
        db_module.engine.dispose()
    logger.info(
        "company file encryption turned %s by %s",
        "on" if data.enabled else "off",
        request.session.get("username") or "operator",
    )
    return {**company_file.status(), "changed": True}
