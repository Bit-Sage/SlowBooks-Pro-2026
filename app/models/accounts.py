# ============================================================================
# Chart of Accounts.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Enum,
    Boolean,
    ForeignKey,
    Numeric,
    DateTime,
    func,
)
from sqlalchemy import event, select
from sqlalchemy.orm import relationship, validates

from app.database import Base


class AccountType(str, enum.Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"
    COGS = "cogs"


class DetailType(str, enum.Enum):
    """QuickBooks' fifteen account types (#390). The detail type refines
    `account_type` and may never contradict it: BASE_TYPE_OF is the pair."""

    BANK = "bank"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"
    OTHER_CURRENT_ASSET = "other_current_asset"
    FIXED_ASSET = "fixed_asset"
    OTHER_ASSET = "other_asset"
    ACCOUNTS_PAYABLE = "accounts_payable"
    CREDIT_CARD = "credit_card"
    OTHER_CURRENT_LIABILITY = "other_current_liability"
    LONG_TERM_LIABILITY = "long_term_liability"
    EQUITY = "equity"
    INCOME = "income"
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"
    EXPENSE = "expense"
    OTHER_INCOME = "other_income"
    OTHER_EXPENSE = "other_expense"


# detail type -> (label, base account type), in the order the chart page
# and the picker list them.
DETAIL_TYPES: dict[DetailType, tuple[str, AccountType]] = {
    DetailType.BANK: ("Bank", AccountType.ASSET),
    DetailType.ACCOUNTS_RECEIVABLE: ("Accounts Receivable", AccountType.ASSET),
    DetailType.OTHER_CURRENT_ASSET: ("Other Current Asset", AccountType.ASSET),
    DetailType.FIXED_ASSET: ("Fixed Asset", AccountType.ASSET),
    DetailType.OTHER_ASSET: ("Other Asset", AccountType.ASSET),
    DetailType.ACCOUNTS_PAYABLE: ("Accounts Payable", AccountType.LIABILITY),
    DetailType.CREDIT_CARD: ("Credit Card", AccountType.LIABILITY),
    DetailType.OTHER_CURRENT_LIABILITY: (
        "Other Current Liability",
        AccountType.LIABILITY,
    ),
    DetailType.LONG_TERM_LIABILITY: ("Long Term Liability", AccountType.LIABILITY),
    DetailType.EQUITY: ("Equity", AccountType.EQUITY),
    DetailType.INCOME: ("Income", AccountType.INCOME),
    DetailType.OTHER_INCOME: ("Other Income", AccountType.INCOME),
    DetailType.COST_OF_GOODS_SOLD: ("Cost of Goods Sold", AccountType.COGS),
    DetailType.EXPENSE: ("Expense", AccountType.EXPENSE),
    DetailType.OTHER_EXPENSE: ("Other Expense", AccountType.EXPENSE),
}
BASE_TYPE_OF: dict[DetailType, AccountType] = {
    d: base for d, (_label, base) in DETAIL_TYPES.items()
}

_TYPE_DEFAULT = {
    AccountType.ASSET: DetailType.OTHER_CURRENT_ASSET,
    AccountType.LIABILITY: DetailType.OTHER_CURRENT_LIABILITY,
    AccountType.EQUITY: DetailType.EQUITY,
    AccountType.INCOME: DetailType.INCOME,
    AccountType.COGS: DetailType.COST_OF_GOODS_SOLD,
    AccountType.EXPENSE: DetailType.EXPENSE,
}
# The bank kinds and the two sub-ledger control accounts pin a detail type.
_BANK_KIND_DETAIL = {"bank": DetailType.BANK, "credit_card": DetailType.CREDIT_CARD}
# Pinned to a bank kind or a control number: never handed down to a
# sub-account. A sub-account of 1100, of a bank or of a card is checked on
# its own detail type alone (the migration keeps the same set).
PINNED_DETAIL_TYPES = frozenset(
    {"bank", "credit_card", "accounts_receivable", "accounts_payable"}
)
# Accounts Receivable / Payable carry an aging and IIF names a customer or
# vendor on their lines; the posting code keeps those on 1100 and 2000 only.
_SUBLEDGER_NUMBER = {
    DetailType.ACCOUNTS_RECEIVABLE: ("1100", AccountType.ASSET),
    DetailType.ACCOUNTS_PAYABLE: ("2000", AccountType.LIABILITY),
}


def _enum(cls, value):
    if value is None or isinstance(value, cls):
        return value
    try:
        return cls(value)
    except ValueError:
        pass
    try:
        return cls[str(value).upper()]  # a stored enum NAME ("ASSET")
    except KeyError:
        raise ValueError(f"unknown {cls.__name__}: {value!r}") from None


def detail_label(detail) -> str:
    d = _enum(DetailType, detail)
    return DETAIL_TYPES[d][0] if d else ""


def default_detail_type(account_type, bank_kind=None, number=None) -> DetailType:
    """The detail type an account gets when nobody chose one: Bank / Credit
    Card for a bank kind, AR / AP on 1100 / 2000, else the type's default."""
    atype = _enum(AccountType, account_type)
    if (
        bank_kind in _BANK_KIND_DETAIL
        and BASE_TYPE_OF[_BANK_KIND_DETAIL[bank_kind]] == atype
    ):
        return _BANK_KIND_DETAIL[bank_kind]
    for detail, (num, base) in _SUBLEDGER_NUMBER.items():
        if str(number or "") == num and atype == base:
            return detail
    return _TYPE_DEFAULT[atype]


def detail_type_problem(detail, account_type, bank_kind=None, number=None):
    """Why this detail type cannot go on this account, or None. The one
    place the pair rules live; the route words its 400 from this and the
    model's insert/update hook falls back to the default on it."""
    from app.services import control_accounts

    d = _enum(DetailType, detail)
    atype = _enum(AccountType, account_type)
    label = DETAIL_TYPES[d][0]
    base = BASE_TYPE_OF[d]
    if base != atype:
        return (
            f"detail_type '{d.value}' is a{'n' if base.value[0] in 'aeiou' else ''} "
            f"{base.value} detail type; the account's type is '{atype.value}'"
        )
    pinned = _BANK_KIND_DETAIL.get(bank_kind)
    if pinned is not None and d != pinned:
        return (
            f"a {bank_kind.replace('_', ' ')} account (bank_kind '{bank_kind}') is "
            f"a {DETAIL_TYPES[pinned][0]}, not {label}"
        )
    if d in _BANK_KIND_DETAIL.values() and pinned != d:
        want = next(k for k, v in _BANK_KIND_DETAIL.items() if v == d)
        return f"a {label} account needs bank_kind '{want}'"
    if d in _SUBLEDGER_NUMBER and str(number or "") != _SUBLEDGER_NUMBER[d][0]:
        return (
            f"{label} is kept for {_SUBLEDGER_NUMBER[d][0]}, the account the "
            f"posting code and the aging use"
        )
    if control_accounts.is_control_number(number):
        want = default_detail_type(atype, bank_kind, number)
        if d != want:
            name, _purpose = control_accounts.describe(number)
            return (
                f"{number} {name} is a control account; its detail type is "
                f"{DETAIL_TYPES[want][0]}"
            )
    return None


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    account_number = Column(String(20), unique=True, nullable=True)
    account_type = Column(Enum(AccountType), nullable=False)
    parent_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )  # sub-account ref
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    is_system = Column(Boolean, default=False)  # seed accounts can't be deleted
    balance = Column(Numeric(15, 2), default=0)
    # "bank" | "credit_card" | NULL. The register, the feed review queue,
    # reconciliation, transfers and every "paid from / deposit to" picker
    # key off this — not off account numbers or names (issue #114).
    bank_kind = Column(String(20), nullable=True)
    # A DetailType value (#390). NOT NULL; an Account(...) built without one
    # gets it from _settle_detail_type below, so the ten-odd paths that
    # create accounts directly need not know about it. The server_default
    # keeps a raw SQL insert (another tool, a manual fix) from failing the
    # NOT NULL; the ORM hook still sets the type-correct value.
    detail_type = Column(
        String(30), nullable=False, server_default="other_current_asset"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent = relationship("Account", remote_side=[id], backref="children")
    transaction_lines = relationship("TransactionLine", back_populates="account")

    @validates("detail_type")
    def _plain_detail_type(self, _key, value):
        # stored as the plain value string on every engine
        d = _enum(DetailType, value)
        return d.value if d else None

    @property
    def detail_type_label(self) -> str:
        return detail_label(self.detail_type)


def _parent_detail(connection, target):
    """The parent's detail type, from the session when it is loaded there
    (it may be pending in this same flush), else from the row."""
    parent = target.__dict__.get("parent")
    if parent is not None:
        return parent.detail_type
    if not target.parent_id:
        return None
    return connection.execute(
        select(Account.__table__.c.detail_type).where(
            Account.__table__.c.id == target.parent_id
        )
    ).scalar()


def _fits(detail, target) -> bool:
    if not detail:
        return False
    try:
        problem = detail_type_problem(
            detail, target.account_type, target.bank_kind, target.account_number
        )
    except ValueError:
        return False
    return problem is None


def _settle_detail_type(connection, target, parent_changed: bool) -> None:
    """Keep detail_type set and consistent on every ORM write: the API has
    already checked and refused, so this only acts on the paths that build
    or change an Account directly (importers, late fees, FX, job costing,
    benefits). A detail type the row declares wins while it fits; a row
    built with none (or one that no longer fits) takes its parent's when
    that fits (QuickBooks' own rule), else the default for its type."""
    if target.account_type is None:
        return
    own = target.detail_type
    if own and not _fits(own, target):
        own = None
    if own and not target.__dict__.get("_detail_type_defaulted"):
        target.detail_type = own
        return
    parent = _parent_detail(connection, target) if parent_changed else None
    if parent and _fits(parent, target):
        target.detail_type = parent
    elif not own:
        target.detail_type = default_detail_type(
            target.account_type, target.bank_kind, target.account_number
        )


@event.listens_for(Account, "before_insert")
def _detail_type_on_insert(_mapper, connection, target):
    # Built with none: the default it gets now may still give way to a
    # parent linked in a later flush of the same session (chart import).
    built_without = not target.detail_type
    _settle_detail_type(
        connection, target, bool(target.parent_id or target.__dict__.get("parent"))
    )
    if built_without:
        target.__dict__["_detail_type_defaulted"] = True


@event.listens_for(Account, "before_update")
def _detail_type_on_update(_mapper, connection, target):
    from sqlalchemy import inspect

    state = inspect(target)
    parent_changed = (
        state.attrs.parent_id.history.has_changes()
        or state.attrs.parent.history.has_changes()
    )
    _settle_detail_type(connection, target, parent_changed)
