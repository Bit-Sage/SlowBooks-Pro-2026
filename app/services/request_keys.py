"""Retry-safe request identity for the payment-recording routes (#292).

A client that may retry — a form after a lost response, an agent, a
script — sends an ``Idempotency-Key`` header. The route reserves the key
before it writes anything, in the same transaction as the payment, and
stores the response under it before the commit:

- the same key with the same body returns the stored response (the
  payment was booked once);
- the same key with a different body is refused (409): a key names one
  intended payment;
- a key whose first delivery is still running is refused (409) until it
  finishes — two concurrent deliveries book one payment;
- no header: an ordinary POST, as before.

Keys are kept for RETENTION_DAYS; a retry later than that is a new request.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.request_keys import RequestKey

HEADER = "Idempotency-Key"
RETENTION_DAYS = 7
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def payload_hash(payload) -> str:
    """The body, canonical: key order and numbers as JSON, so the same
    intended payment hashes the same however the client spelled it."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Reservation:
    """The row reserved for this delivery; ``finish`` fills it."""

    def __init__(self, row: RequestKey):
        self.row = row


def reserve(db: Session, request: Request, route: str, payload, actor: str | None):
    """Before the write. Returns None (no key: an ordinary request), a
    JSONResponse (a replay: send it as the answer), or a Reservation to
    ``finish`` before the commit. Raises 409 for a reused key with another
    body, or a first delivery still running."""
    key = request.headers.get(HEADER)
    if not key:
        return None
    key = key.strip()
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=400,
            detail=f"{HEADER} must be 1–128 characters of letters, digits, '-', '_', '.' or ':'.",
        )
    digest = payload_hash(payload)
    _sweep(db)
    sp = db.begin_nested()
    try:
        row = RequestKey(route=route, key=key, payload_hash=digest, actor=actor)
        db.add(row)
        db.flush()
        sp.commit()
        return Reservation(row)
    except IntegrityError:
        sp.rollback()
    existing = db.query(RequestKey).filter_by(route=route, key=key).first()
    if existing is None:  # the twin rolled back between our insert and this read
        raise HTTPException(
            status_code=409,
            detail="This request is being processed; send it again in a moment.",
        )
    if existing.payload_hash != digest:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{HEADER} {key!r} was already used for a different request on this "
                "route. Use a new key for a new payment."
            ),
        )
    if existing.response_json is None:
        raise HTTPException(
            status_code=409,
            detail="This request is being processed; send it again in a moment.",
        )
    return JSONResponse(
        status_code=existing.status_code or 200,
        content=json.loads(existing.response_json),
        headers={"X-Replayed": "true"},
    )


def finish(db: Session, reservation, status_code: int, body) -> None:
    """After the write, before the commit: the response the key will
    answer with from now on."""
    if reservation is None:
        return
    reservation.row.status_code = status_code
    reservation.row.response_json = json.dumps(body, default=str)
    db.flush()


def _sweep(db: Session) -> None:
    """Rows past the retention window go; a retry that late is new."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    db.query(RequestKey).filter(RequestKey.created_at < cutoff).delete(
        synchronize_session=False
    )
