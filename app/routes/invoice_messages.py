# ============================================================================
# Invoice Messages — the saved notes an invoice can carry (#392)
#
# A small per-company list kept under Settings; the invoice form offers it
# beside Notes and copies the chosen body in. Writes are an administrator's
# (_ADMIN_WRITE_PREFIXES in app/main.py), as the settings are.
# ============================================================================

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoice_messages import InvoiceMessage
from app.routes._helpers import get_or_404, reject_null_updates
from app.schemas.invoice_messages import (
    GIVING_STATEMENT_BODY_MAX,
    InvoiceMessageCreate,
    InvoiceMessageResponse,
    InvoiceMessageUpdate,
)

router = APIRouter(prefix="/api/invoice-messages", tags=["invoice-messages"])


def default_message_body(db: Session, kind: str = "invoice") -> Optional[str]:
    """The default message's text for ``kind`` — a new invoice's Notes, or
    the giving statement's opening message (#415); None when that kind has
    no default."""
    row = (
        db.query(InvoiceMessage.body)
        .filter(InvoiceMessage.is_default.is_(True), InvoiceMessage.kind == kind)
        .order_by(InvoiceMessage.id)
        .first()
    )
    return row[0] if row else None


def _check_body_cap(kind: str, body: Optional[str]) -> None:
    """A giving-statement opening message has a length cap so a pasted
    letter cannot run off the page; invoice messages stay uncapped."""
    if kind == "giving_statement" and body and len(body) > GIVING_STATEMENT_BODY_MAX:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A giving-statement message is at most "
                f"{GIVING_STATEMENT_BODY_MAX} characters."
            ),
        )


def _clear_default(db: Session, kind: str, keep_id: Optional[int] = None) -> None:
    """One default at a time per kind: unset every other row of the same
    kind in the same transaction as the one being set, so neither list
    shows two defaults.

    Each row is flipped through the ORM, not a bulk Query.update, so the
    after_flush audit hook logs the old default turning off (it only sees
    rows that pass through session.dirty)."""
    q = db.query(InvoiceMessage).filter(
        InvoiceMessage.is_default.is_(True), InvoiceMessage.kind == kind
    )
    if keep_id is not None:
        q = q.filter(InvoiceMessage.id != keep_id)
    for row in q.all():
        row.is_default = False


def _name_taken(db: Session, name: str, except_id: Optional[int] = None) -> bool:
    q = db.query(InvoiceMessage.id).filter(InvoiceMessage.name == name)
    if except_id is not None:
        q = q.filter(InvoiceMessage.id != except_id)
    return q.first() is not None


@router.get("", response_model=list[InvoiceMessageResponse])
def list_messages(kind: str = "invoice", db: Session = Depends(get_db)):
    # The invoice form and the Settings invoice list ask for nothing and get
    # kind='invoice', unchanged from #392; the giving-statement list asks for
    # ?kind=giving_statement. The default first, then by name.
    return (
        db.query(InvoiceMessage)
        .filter(InvoiceMessage.kind == kind)
        .order_by(InvoiceMessage.is_default.desc(), InvoiceMessage.name)
        .all()
    )


@router.post("", response_model=InvoiceMessageResponse, status_code=201)
def create_message(data: InvoiceMessageCreate, db: Session = Depends(get_db)):
    if _name_taken(db, data.name):
        raise HTTPException(
            status_code=400, detail="A message with this name already exists"
        )
    _check_body_cap(data.kind, data.body)
    if data.is_default:
        _clear_default(db, data.kind)
    msg = InvoiceMessage(**data.model_dump())
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@router.put("/{message_id}", response_model=InvoiceMessageResponse)
def update_message(
    message_id: int, data: InvoiceMessageUpdate, db: Session = Depends(get_db)
):
    msg = get_or_404(db, InvoiceMessage, message_id, "Message")
    changes = reject_null_updates(InvoiceMessage, data.model_dump(exclude_unset=True))
    if "name" in changes and _name_taken(db, changes["name"], except_id=msg.id):
        raise HTTPException(
            status_code=400, detail="A message with this name already exists"
        )
    # The kind after this edit decides the cap and which default list to clear.
    kind = changes.get("kind", msg.kind)
    _check_body_cap(kind, changes.get("body", msg.body))
    if changes.get("is_default"):
        _clear_default(db, kind, keep_id=msg.id)
    for key, val in changes.items():
        setattr(msg, key, val)
    db.commit()
    db.refresh(msg)
    return msg


@router.delete("/{message_id}")
def delete_message(message_id: int, db: Session = Depends(get_db)):
    # Deleting the default is allowed: a new invoice then starts from the
    # Default Invoice Notes setting, as it did before there were messages.
    msg = get_or_404(db, InvoiceMessage, message_id, "Message")
    db.delete(msg)
    db.commit()
    return {"status": "deleted"}
