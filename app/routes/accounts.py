import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import (
    BASE_TYPE_OF,
    PINNED_DETAIL_TYPES,
    Account,
    DETAIL_TYPES,
    DetailType,
    default_detail_type,
    detail_label,
    detail_type_problem,
)
from app.services import control_accounts
from app.models.transactions import TransactionLine
from app.schemas.accounts import AccountCreate, AccountUpdate, AccountResponse
from app.routes._helpers import get_or_404, reject_null_updates

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _reject_duplicate_number(db: Session, number, exclude_id=None):
    """account_number is UNIQUE. Without this the violation surfaces from the
    database as an unhandled IntegrityError, i.e. an opaque HTTP 500 that
    names neither the field nor the account already using it."""
    if not number:
        return
    q = db.query(Account).filter(Account.account_number == number)
    if exclude_id is not None:
        q = q.filter(Account.id != exclude_id)
    clash = q.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Account number {number} is already used by "
                f"'{clash.name}'. Account numbers must be unique."
            ),
        )


# Digits, optionally in dotted or dashed groups for a sub-account (6150.1,
# 6150-01) — the pattern the seeded chart and the chart importer use. "ABC"
# and a blank number were accepted, and a blank one then listed as
# " - Name" in every picker (2.17.3 exploratory test, W-L4). Charts brought
# in by an importer keep whatever numbers they carry; this is the rule for
# numbers typed on the form or sent to the API.
_ACCOUNT_NUMBER_RE = re.compile(r"\d+(?:[.-]\d+)*")


def _check_account_number(number) -> None:
    """A number is optional (#332: many companies don't number their
    accounts; the books never needed one). Given, it is digits, like 6150,
    or 6150.1 / 6150-01 for a sub-account."""
    if not number:
        return
    if len(number) > 20 or not _ACCOUNT_NUMBER_RE.fullmatch(number):
        raise HTTPException(
            status_code=400,
            detail=(
                f'"{number[:40]}" is not an account number. Use digits, like '
                "6150; a sub-account can use 6150.1 or 6150-01."
            ),
        )


_BANK_KIND_FOR_TYPE = {"bank": "asset", "credit_card": "liability"}


def _check_bank_kind(bank_kind, account_type) -> None:
    """A bank is an asset, a card is a liability; anything else is a
    mistake the picker would propagate everywhere."""
    if bank_kind is None:
        return
    want = _BANK_KIND_FOR_TYPE.get(bank_kind)
    have = getattr(account_type, "value", account_type)
    if want != have:
        raise HTTPException(
            status_code=400,
            detail=f"bank_kind {bank_kind!r} needs account_type {want!r}, not {have!r}",
        )


def _bank_history(db: Session, account: Account) -> list[str]:
    """What hangs off a bank or card account as a register: reconciliations,
    bank-feed rows, register entries and transfers. Each is keyed off
    bank_kind; clearing it would orphan them."""
    from app.models.banking import BankAccount, BankTransaction, Reconciliation
    from app.models.transactions import Transaction

    feeds = [
        i
        for (i,) in db.query(BankAccount.id).filter(
            BankAccount.account_id == account.id
        )
    ]
    found = []
    recs = (
        db.query(func.count(Reconciliation.id))
        .filter(
            (Reconciliation.account_id == account.id)
            | (Reconciliation.bank_account_id.in_(feeds or [-1]))
        )
        .scalar()
    )
    if recs:
        found.append(f"{recs} reconciliation(s)")
    rows = (
        db.query(func.count(BankTransaction.id))
        .filter(BankTransaction.bank_account_id.in_(feeds or [-1]))
        .scalar()
    )
    if rows:
        found.append(f"{rows} bank-feed row(s)")
    entries = (
        db.query(func.count(func.distinct(Transaction.id)))
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == account.id,
            Transaction.source_type.in_(("bank_entry", "transfer")),
        )
        .scalar()
    )
    if entries:
        found.append(f"{entries} register entr{'y' if entries == 1 else 'ies'}")
    return found


def _check_bank_kind_kept(db: Session, account: Account, fields: dict) -> None:
    """A bank or card account in use keeps its bank kind: the register, its
    reconciliations and its feed all key off it (#390 review)."""
    if "bank_kind" not in fields or not account.bank_kind:
        return
    if fields["bank_kind"] == account.bank_kind:
        return
    used = _bank_history(db, account)
    if used:
        kind = account.bank_kind.replace("_", " ")
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{account.name}' is a {kind} account with {', '.join(used)}; "
                f"changing its kind would take its register, reconciliations "
                f"and feed away. Make a new account for the other use and "
                f"deactivate this one."
            ),
        )


def _named(acct: Account) -> str:
    return " ".join(p for p in (acct.account_number, acct.name) if p)


def _move_out_first(child: Account, parent: Account) -> str:
    """The way out of a type change a parent and its sub-account block for
    each other: base types are not cascaded."""
    return (
        f" Move {_named(child) or 'the sub-account'} out from under "
        f"{_named(parent)} first (set its parent to none), change the types, "
        f"then put it back."
    )


def _sub_account_refusal(
    parent: Account, why: str | None = None, child: Account | None = None, atype=None
) -> HTTPException:
    label = detail_label(parent.detail_type)
    detail = (
        f"a sub-account of {_named(parent)} ({label}) must be "
        f"a{'n' if label[0] in 'AEIOU' else ''} {label} too"
    ) + (f": {why}" if why else "")
    base = BASE_TYPE_OF[DetailType(parent.detail_type)]
    if (
        child is not None
        and atype is not None
        and getattr(atype, "value", atype) != (base.value)
    ):
        detail += "." + _move_out_first(child, parent)
    return HTTPException(status_code=400, detail=detail)


def _shares_parent(parent: Account | None) -> bool:
    """A sub-account matches its parent's detail type, except under a
    pinned one (1100, 2000, a bank, a card): those are checked alone."""
    return parent is not None and parent.detail_type not in PINNED_DETAIL_TYPES


def _create_detail_type(db: Session, data: AccountCreate) -> str:
    """The detail type a new account gets (#390): the one sent, else its
    parent's, else the default for its type and bank kind. A sub-account
    shares its parent's detail type; the pair with the type, the bank kind
    and the control numbers is checked in detail_type_problem."""
    parent = db.get(Account, data.parent_id) if data.parent_id else None
    if _shares_parent(parent):
        if data.detail_type is not None and data.detail_type.value != (
            parent.detail_type
        ):
            raise _sub_account_refusal(parent)
        why = detail_type_problem(
            parent.detail_type, data.account_type, data.bank_kind, data.account_number
        )
        if why:
            raise _sub_account_refusal(parent, why)
        return parent.detail_type
    detail = data.detail_type or default_detail_type(
        data.account_type, data.bank_kind, data.account_number
    )
    why = detail_type_problem(
        detail, data.account_type, data.bank_kind, data.account_number
    )
    if why:
        raise HTTPException(status_code=400, detail=why)
    return getattr(detail, "value", detail)


def _cascade_detail_type(account: Account, old: str, new: str, seen: set) -> None:
    """A parent's new detail type goes down to the sub-accounts that shared
    the old one; one that cannot take it refuses the change."""
    if old in PINNED_DETAIL_TYPES or new in PINNED_DETAIL_TYPES:
        return  # nothing shares a pinned detail type by inheritance
    for child in account.children:
        if child.id in seen or child.detail_type != old:
            continue
        seen.add(child.id)
        why = detail_type_problem(
            new, child.account_type, child.bank_kind, child.account_number
        )
        if why:
            detail = (
                f"its sub-account {_named(child)} would have to become "
                f"{detail_label(new)} too, and cannot: {why}."
            )
            if BASE_TYPE_OF[DetailType(new)] != BASE_TYPE_OF[DetailType(old)]:
                detail += _move_out_first(child, account)
            raise HTTPException(status_code=400, detail=detail)
        _cascade_detail_type(child, old, new, seen)
        child.detail_type = new


def _update_detail_type(db: Session, account: Account, fields: dict) -> None:
    """Settle fields["detail_type"] for a PUT (#390). Omitted, the current
    detail type stays while it still fits; a type or bank-kind change that
    it no longer fits resets it to the default, and a new parent's is taken.
    Control accounts keep theirs; a sub-account matches its parent."""
    atype = fields.get("account_type", account.account_type)
    kind = fields.get("bank_kind", account.bank_kind)
    number = fields.get("account_number", account.account_number)
    parent_id = fields.get("parent_id", account.parent_id)
    parent = db.get(Account, parent_id) if parent_id else None
    current = account.detail_type
    if fields.get("detail_type") is not None:
        detail = fields["detail_type"].value
    elif (
        "parent_id" in fields
        and _shares_parent(parent)
        and not control_accounts.is_control_number(account.account_number)
    ):
        # a control account never takes a parent's: the sub-account check
        # below says why it cannot go there
        detail = parent.detail_type
    elif detail_type_problem(current, atype, kind, number) is None:
        detail = current
    else:
        detail = default_detail_type(atype, kind, number).value
    asked = {"detail_type", "bank_kind", "account_type"} & set(fields)
    if (
        detail != current
        and asked
        and control_accounts.is_control_number(account.account_number)
    ):
        name, purpose = control_accounts.describe(account.account_number)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account.account_number} {name} is a control account — the "
                f"software finds it by its number to post {purpose}. Its detail "
                f"type stays {detail_label(current)}. You can rename it."
            ),
        )
    if _shares_parent(parent) and ("parent_id" in fields or detail != current):
        parent_why = detail_type_problem(parent.detail_type, atype, kind, number)
        if detail != parent.detail_type or parent_why:
            raise _sub_account_refusal(parent, parent_why, account, atype)
    why = detail_type_problem(detail, atype, kind, number)
    if why:
        raise HTTPException(status_code=400, detail=why)
    if detail != current:
        _cascade_detail_type(account, current, detail, {account.id})
    if detail != current or "detail_type" in fields:
        fields["detail_type"] = detail


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    active_only: bool = False,
    account_type: str = None,
    bank: bool = False,
    detail_type: str = None,
    db: Session = Depends(get_db),
):
    """`?bank=1` lists the bank and credit-card accounts (bank_kind set) —
    what the register, the transfer form and every paid-from / deposit-to
    picker use. `?detail_type=long_term_liability` filters on the detail
    type (#390)."""
    q = db.query(Account)
    if active_only:
        q = q.filter(Account.is_active)
    if account_type:
        q = q.filter(Account.account_type == account_type)
    if bank:
        q = q.filter(Account.bank_kind.isnot(None))
    if detail_type:
        q = q.filter(Account.detail_type == detail_type)
    # By number, and accounts without one (a chart imported from QuickBooks
    # Online, whose accounts carry none) by name, so a sub-account named
    # Parent:Child sits with its parent.
    return _in_company_words(
        db, q.order_by(nullslast(Account.account_number), Account.name).all()
    )


# The seeded accounts a pay run, a PTO accrual or a benefits posting finds
# by number, with nothing posted to them until the first run (2.23.5 round
# 2, W-16 / NEW-139: the cleanup took them, and the next pay run either
# failed with a 500 or posted wages to Advertising). They stay.
_FOUND_BY_NUMBER = {
    "6110": "payroll posts wages to it",
    "6140": "payroll posts reimbursements to it",
    "6160": "PTO accruals post to it",
    "6950": "payroll's fallback for reimbursements",
    "2300": "payroll liabilities post to it",
    "2310": "payroll posts federal withholding to it",
    "2320": "payroll posts state withholding to it",
    "2330": "payroll posts Social Security to it",
    "2340": "payroll posts Medicare to it",
    "2350": "payroll posts FUTA to it",
    "2360": "payroll posts SUTA to it",
    "2370": "payroll posts other withholdings to it",
    "2380": "benefits post to it",
    "2390": "PTO accruals post to it",
}


def _seeded_unused(db: Session) -> tuple[list[dict], list[dict]]:
    """The seeded accounts nobody uses, and the seeded ones that stay and
    why (#332): a control account, postings, sub-accounts, or a row that
    names it (a register, an item, a setting)."""
    removable, kept = [], []
    seeded = (
        db.query(Account)
        .filter(Account.is_system.is_(True))
        .order_by(nullslast(Account.account_number), Account.name)
        .all()
    )
    children = {
        pid
        for (pid,) in db.query(Account.parent_id).filter(Account.parent_id.isnot(None))
    }
    posted = {aid for (aid,) in db.query(TransactionLine.account_id).distinct()}
    for a in seeded:
        row = {"id": a.id, "account_number": a.account_number or "", "name": a.name}
        if control_accounts.is_control_number(a.account_number):
            kept.append({**row, "reason": "a control account"})
        elif a.account_number in _FOUND_BY_NUMBER:
            kept.append({**row, "reason": _FOUND_BY_NUMBER[a.account_number]})
        elif a.id in posted:
            kept.append({**row, "reason": "has postings"})
        elif a.id in children:
            kept.append({**row, "reason": "has sub-accounts"})
        else:
            others = [
                (t, n)
                for t, n in _referencing_rows(db, a.id)
                if t != "transaction lines"
            ]
            if others:
                kept.append(
                    {
                        **row,
                        "reason": "in use: "
                        + ", ".join(f"{n} in {t}" for t, n in others),
                    }
                )
            else:
                removable.append(row)
    return removable, kept


@router.get("/detail-types")
def list_detail_types():
    """QuickBooks' fifteen account types, in picker order, each with its
    base type (#390). Accounts Receivable and Payable are kept for 1100 and
    2000 (`control_only`), so the picker offers them nowhere else."""
    return [
        {
            "value": d.value,
            "label": label,
            "account_type": base.value,
            "control_only": d.value in ("accounts_receivable", "accounts_payable"),
        }
        for d, (label, base) in DETAIL_TYPES.items()
    ]


@router.get("/unused-seeded")
def unused_seeded(db: Session = Depends(get_db)):
    """A dry run: the seeded accounts that can go, and the ones that stay."""
    removable, kept = _seeded_unused(db)
    return {"removable": removable, "kept": kept}


@router.post("/unused-seeded/remove")
def remove_unused_seeded(db: Session = Depends(get_db)):
    """Remove every seeded account nobody uses, in one step; the result
    names what went and what stayed (#332)."""
    removable, kept = _seeded_unused(db)
    removed = []
    for row in removable:
        acct = db.get(Account, row["id"])
        if acct is None:
            continue
        sp = db.begin_nested()
        try:
            db.delete(acct)
            db.flush()
            sp.commit()
            removed.append(row)
        except IntegrityError:
            sp.rollback()
            kept.append({**row, "reason": "still referenced"})
    db.commit()
    return {"removed": removed, "kept": kept}


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    return _in_company_words(db, [get_or_404(db, Account, account_id)])[0]


def _in_company_words(db: Session, accounts):
    """The control-account purpose ("what customers owe — every invoice and
    payment") is written in the business words the registry keeps; the
    chart page prints it beside the account. Say it in the company's."""
    from app.services.terminology import terms_from_db

    terms = terms_from_db(db)
    out = [AccountResponse.model_validate(a) for a in accounts]
    if terms.is_nonprofit:
        for r in out:
            if r.control_purpose:
                r.control_purpose = terms.text(r.control_purpose)
    return out


@router.post("", response_model=AccountResponse, status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    _check_bank_kind(data.bank_kind, data.account_type)
    data.detail_type = _create_detail_type(db, data)
    _check_account_number(data.account_number)
    _reject_duplicate_number(db, data.account_number)
    account = Account(**data.model_dump())
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Account violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)
    fields = reject_null_updates(Account, data.model_dump(exclude_unset=True))

    # Issue #119: the posting code resolves these accounts BY NUMBER, so
    # renumbering one makes every later document skip its journal entry —
    # silently, with a trial balance that still balances. Renaming is fine
    # and stays allowed; only the number and the type are load-bearing.
    # (Not gated on is_system: every seeded account carries that flag, and
    # renumbering an ordinary expense account is a reasonable request.)
    if control_accounts.is_control_number(account.account_number):
        name, purpose = control_accounts.describe(account.account_number)
        for field, what in (("account_number", "number"), ("account_type", "type")):
            if field in fields and fields[field] != getattr(account, field):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{account.account_number} {name} is a control account — "
                        f"the software finds it by its number to post {purpose}. "
                        f"Changing its {what} would stop new documents reaching "
                        f"the ledger. You can rename it."
                    ),
                )

    if "account_number" in fields:
        # Checked only when it changes: an account an importer brought in
        # with its own number (or none) can still be renamed from the form.
        if fields["account_number"] != account.account_number:
            _check_account_number(fields["account_number"])
        _reject_duplicate_number(db, fields["account_number"], exclude_id=account_id)
    if fields.get("parent_id") == account_id:
        raise HTTPException(
            status_code=400, detail="An account cannot be its own parent."
        )
    if "bank_kind" in fields or "account_type" in fields:
        _check_bank_kind(
            fields.get("bank_kind", account.bank_kind),
            fields.get("account_type", account.account_type),
        )
    _check_bank_kind_kept(db, account, fields)
    _update_detail_type(db, account, fields)

    for key, val in fields.items():
        setattr(account, key, val)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Update violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    return account


def _referencing_rows(db: Session, account_id: int) -> list[tuple[str, int]]:
    """[(table, count)] for every row in the database pointing at this
    account, derived from the schema rather than a hand-kept list.

    Thirty-five columns across seventeen models reference `accounts.id`, and
    that number grows with every feature — vendor credits added one this
    week. A list maintained by hand would be wrong within a release, and the
    symptom of it being wrong is a foreign-key violation surfacing as a 500.
    Walking the metadata cannot fall behind the schema.
    """
    from app.database import Base

    found: list[tuple[str, int]] = []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name != "accounts":
                continue
            n = (
                db.query(func.count())
                .select_from(table)
                .filter(fk.parent == account_id)
                .scalar()
            ) or 0
            if n:
                found.append((table.name.replace("_", " "), n))
    return found


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)

    # Gated on the control-account registry, NOT on is_system.
    #
    # Every one of the 57 accounts a new company is seeded with carries
    # is_system, so gating on it refused all of them — which left an operator
    # with someone else's chart of accounts and no way to shrink it: delete
    # refused, and there was no deactivate control on the page either. That
    # is the same wrong flag 2.10.2 found hiding the Edit button, in a second
    # place (issue #139, reported by tresero coming from hledger).
    #
    # Fifteen numbers genuinely cannot go: the posting code resolves them
    # literally, and a document that cannot find one is #119. Those can be
    # renamed, not removed. The other forty-two are ordinary accounts.
    if control_accounts.is_control_number(account.account_number):
        name, purpose = control_accounts.describe(account.account_number)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account.account_number} {name} is a control account — the "
                f"software posts to it by number for {purpose}, so it cannot "
                f"be deleted. You can rename it, or deactivate it to hide it "
                f"from new entries."
            ),
        )

    # An account carrying ledger history must not be deleted — the postings
    # would lose their anchor. Refusing is correct; the bug was that the
    # refusal arrived as a raw foreign-key violation (HTTP 500) instead of a
    # business rule the caller can act on.
    posted = (
        db.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .count()
    )
    if posted:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' has {posted} posted transaction line(s) and "
                f"cannot be deleted. Deactivate it instead (set is_active=false) "
                f"to hide it from new entries while preserving history."
            ),
        )

    # Anything else still pointing at it — an item's income account, a
    # vendor's default expense account, a bank feed, a budget line. Named,
    # because "referenced by other records" tells the operator nothing about
    # where to go and undo it.
    others = [
        (t, n) for t, n in _referencing_rows(db, account_id) if t != "transaction lines"
    ]
    if others:
        where = ", ".join(f"{n} in {t}" for t, n in others)
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' is still in use: {where}. Point those at "
                f"another account first, or deactivate this one to hide it "
                f"from new entries."
            ),
        )

    db.delete(account)
    try:
        db.commit()
    except IntegrityError:
        # Any of the other tables referencing accounts.id — belt and braces so
        # no constraint can ever leak as a 500 from this endpoint again.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' is still referenced by other records and "
                f"cannot be deleted. Deactivate it instead."
            ),
        )
    return {"message": "Account deleted"}
