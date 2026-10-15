# ============================================================================
# User management (Server Edition) — admin-only via the RBAC middleware
# (/api/users is an admin write prefix; reads are role-gated in-route so
# non-admins never enumerate accounts either).
#
# No DELETE on purpose: users deactivate, they don't disappear — their
# username stays meaningful in the audit trail forever.
# ============================================================================

import re
from typing import Optional

from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.users import ROLE_ADMIN, VALID_ROLES, User
from app.services.auth import MIN_PASSWORD_LEN, hash_password


def _utc_iso(stamp):
    """A stored timestamp as ISO text with its zone: SQLite's are naive UTC,
    and a browser reads a naive stamp as local, which put "Last login" and
    "Last used" on tomorrow's date from 7 pm Central (2.23.2 gate, NEW-110).
    The backups list sends "+00:00" and is right; so do these now."""
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


router = APIRouter(prefix="/api/users", tags=["users"])

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,49}$")


def _require_admin(request: Request) -> None:
    if (request.session.get("role") or "admin") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": _utc_iso(u.created_at),
        "last_login_at": _utc_iso(u.last_login_at),
    }


def _other_active_admin_exists(db: Session, user: User) -> bool:
    return (
        db.query(User)
        .filter(User.role == ROLE_ADMIN, User.is_active, User.id != user.id)
        .count()
        > 0
    )


class UserCreate(StrictModel):
    username: str = Field(..., min_length=3, max_length=50)
    display_name: str = Field("", max_length=200)
    password: str = Field(..., min_length=1, max_length=512)
    role: str = Field(...)


class UserUpdate(StrictModel):
    display_name: Optional[str] = Field(None, max_length=200)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=512)


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return [_user_out(u) for u in db.query(User).order_by(User.id).all()]


@router.post("", status_code=201)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    username = payload.username.strip().lower()
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-50 characters: letters, digits, . _ -",
        )
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(payload.password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
        )
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(
        username=username,
        display_name=payload.display_name.strip() or username.title(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return _user_out(user)


@router.put("/{user_id}")
def update_user(
    user_id: int, payload: UserUpdate, request: Request, db: Session = Depends(get_db)
):
    _require_admin(request)
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    demoting = payload.role is not None and payload.role != ROLE_ADMIN
    deactivating = payload.is_active is False
    if (
        user.role == ROLE_ADMIN
        and user.is_active
        and (demoting or deactivating)
        and not _other_active_admin_exists(db, user)
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot remove the last active admin",
        )

    if payload.role is not None:
        if payload.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role")
        user.role = payload.role
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        if len(payload.password) < MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
            )
        user.password_hash = hash_password(payload.password)
    db.commit()
    return _user_out(user)
