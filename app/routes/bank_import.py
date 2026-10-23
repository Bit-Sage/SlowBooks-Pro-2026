# ============================================================================
# Bank Feed Import — OFX/QFX + CSV file upload and import
# Feature 18: Upload → preview → confirm → auto-match by amount/date
# ============================================================================

import codecs
import csv
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.services.ofx_import import (
    EMPTY_TEXT,
    UNREADABLE_TEXT,
    UnreadableStatement,
    import_transactions,
    parse_ofx,
)
from app.services.bank_csv_import import parse_csv, import_csv_transactions
from app.services.upload_limits import read_limited

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


# A byte-order mark names the encoding of a text export outright; decode by
# it before the binary-file check, so a real UTF-16 CSV (whose bytes are full
# of NULs) isn't mistaken for junk (Testing#101). UTF-16 covers both LE and
# BE, each with its own BOM.
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


async def _read_statement(file: UploadFile, label: str, bom: str = "utf-8") -> str:
    """The uploaded statement as text, or a 400 saying why it can't be:
    an empty file, or a binary one (a PDF, a spreadsheet, random bytes)
    that no statement parser here reads (Testing#101)."""
    content = await read_limited(file, label=label)
    # Decode by a byte-order mark first: a UTF-16 export's bytes are full of
    # NULs by design, so this has to happen before the NUL-padding strip and
    # the binary-file guard below would ever see them.
    for marker, encoding in _BOMS:
        if content.startswith(marker):
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError:
                break
            # A file that is nothing but a BOM decodes to "" (the marker is
            # the encoding declaration, not content), so it has to answer the
            # empty-file sentence like any other empty file rather than fall
            # through as a 200 with an error in the body or an "unreadable".
            if not text.strip():
                raise HTTPException(status_code=400, detail=EMPTY_TEXT)
            return text
    # A statement exported to a plain-text file sometimes carries trailing
    # NUL padding; drop it so a complete file isn't taken for binary junk.
    content = content.rstrip(b"\x00")
    if not content.strip():
        raise HTTPException(status_code=400, detail=EMPTY_TEXT)
    if b"\x00" in content[:8192] or content.startswith(b"%PDF"):
        logger.info("bank file rejected as binary (%d bytes)", len(content))
        raise HTTPException(status_code=400, detail=UNREADABLE_TEXT)
    try:
        return content.decode(bom)
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _read_ofx(text: str) -> list[dict]:
    try:
        return parse_ofx(text)
    except UnreadableStatement as problem:
        raise HTTPException(status_code=400, detail=problem.user_text) from None


def _unreadable_csv(exc: csv.Error) -> HTTPException:
    # csv.Error's text can quote the file; log its class only.
    logger.info("bank CSV unreadable: %s", type(exc).__name__)
    return HTTPException(status_code=400, detail=UNREADABLE_TEXT)


@router.post("/preview")
async def preview_ofx(file: UploadFile = File(...)):
    """Parse OFX/QFX file and return preview of transactions."""
    text = await _read_statement(file, label="Bank file")
    transactions = _read_ofx(text)
    return {
        "count": len(transactions),
        "transactions": [
            {
                "fitid": t.get("fitid", ""),
                "date": t["date"].isoformat(),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "memo": t.get("memo", ""),
            }
            for t in transactions
        ],
    }


@router.post("/import/{bank_account_id}")
async def import_ofx(
    bank_account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """Import OFX/QFX transactions into a bank account."""
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    text = await _read_statement(file, label="Bank file")
    transactions = _read_ofx(text)
    result = import_transactions(db, bank_account_id, transactions)
    return result


def _mapping(raw: Optional[str]) -> Optional[dict]:
    """The import dialog's column choices, sent as a JSON form field."""
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except ValueError:
        mapping = None
    if not isinstance(mapping, dict):
        raise HTTPException(
            status_code=400,
            detail="The column choices could not be read. Choose them again.",
        )
    return mapping


@router.post("/preview-csv")
async def preview_csv(
    file: UploadFile = File(...), mapping: Optional[str] = Form(None)
):
    """Parse CSV bank statement and return preview of transactions. An
    unrecognised layout answers with its columns and a few rows, for the
    dialog's mapping step; `mapping` (JSON) is that step's answer."""
    text = await _read_statement(file, label="CSV file", bom="utf-8-sig")
    try:
        result = parse_csv(text, _mapping(mapping))
    except csv.Error as exc:
        raise _unreadable_csv(exc) from None
    if result["error"]:
        out = {
            "format": result["format"],
            "error": result["error"],
            "count": 0,
            "transactions": [],
        }
        for key in ("header_row", "sample", "has_header", "suggested"):
            if key in result:
                out[key] = result[key]
        return out

    return {
        "format": result["format"],
        "unread": result.get("unread", 0),
        "count": len(result["transactions"]),
        "transactions": [
            {
                "date": (
                    t["date"].isoformat()
                    if hasattr(t["date"], "isoformat")
                    else str(t["date"])
                ),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "description": t.get("description", ""),
                "check_number": t.get("check_number"),
                "fee": float(t["fee"]) if t.get("fee") else None,
            }
            for t in result["transactions"]
        ],
    }


@router.post("/import-csv/{bank_account_id}")
async def import_csv(
    bank_account_id: int,
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Import CSV bank transactions into a bank account.

    Auto-detects format (Bank of America detail, Chase checking/credit,
    PayPal, or a header naming date / description / amount), or takes the
    dialog's column `mapping` (JSON) for a layout detection missed.
    Deduplicates by content-derived import_id (re-imports and overlapping
    exports skip; legitimate same-day duplicates still import).
    Auto-applies bank rules after import.
    """
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    text = await _read_statement(file, label="CSV file", bom="utf-8-sig")
    try:
        result = import_csv_transactions(
            db, bank_account_id, text, mapping=_mapping(mapping)
        )
    except csv.Error as exc:
        raise _unreadable_csv(exc) from None
    return result
