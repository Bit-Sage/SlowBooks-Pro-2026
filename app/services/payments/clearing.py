# ============================================================================
# Clearing-account helpers for online payments (#583).
#
# When a provider's clearing setting is on, a captured payment posts to that
# provider's asset clearing account (not Undeposited Funds) and the provider's
# processing fee posts to a shared expense account in the same entry. The
# accounts are created on demand, the way Opening Balance Equity (3900) is, and
# a company can point a setting at an account of its own instead.
#
# Step 2 (#584, recording the payout and matching it to the bank feed) builds
# on these helpers.
# ============================================================================

import logging
from typing import Mapping

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.services.accounting import ensure_account
from app.services.control_accounts import is_control
from app.services.settings_service import get_setting_raw

logger = logging.getLogger(__name__)

PROVIDER_NAMES = ("stripe", "paypal", "square")

# Human-facing provider names (PayPal keeps its capital P).
DISPLAY_NAMES = {"stripe": "Stripe", "paypal": "PayPal", "square": "Square"}

# Suggested account number + name per provider's clearing account. The number
# is kept only when the chart hasn't used it (ensure_account), so an imported
# chart that already owns 1210 gets an unnumbered "Stripe Clearing" instead.
_CLEARING_ACCOUNTS = {
    "stripe": ("1210", "Stripe Clearing"),
    "paypal": ("1220", "PayPal Clearing"),
    "square": ("1230", "Square Clearing"),
}

# One expense account shared by all three providers.
FEE_ACCOUNT_NUMBER = "6210"
FEE_ACCOUNT_NAME = "Merchant Account Fees"


def clearing_enabled(settings: Mapping[str, str], provider_name: str) -> bool:
    """True when this provider posts captured payments to a clearing account."""
    return settings.get(f"{provider_name}_clearing_enabled") == "true"


def clearing_enabled_db(db: Session, provider_name: str) -> bool:
    """clearing_enabled read straight from the settings table — for the
    recorder, which has no settings dict in hand."""
    return get_setting_raw(db, f"{provider_name}_clearing_enabled") == "true"


def _is_valid_clearing_account(acct: Account | None) -> bool:
    """A chosen clearing account must still be usable: an active, ordinary
    asset account that is not a bank-feed account and not a control account
    (1100 A/R, 1200 Undeposited Funds, …). The same rule the settings guard
    applies at save — re-checked here because the chart can change under a
    stored id."""
    if acct is None:
        return False
    if acct.account_type != AccountType.ASSET or acct.bank_kind:
        return False
    if acct.is_active is False:
        return False
    if is_control(acct.account_number, acct.account_type):
        return False
    return True


def _is_valid_fee_account(acct: Account | None) -> bool:
    """A chosen fee account must still be an active expense account."""
    if acct is None:
        return False
    if acct.account_type != AccountType.EXPENSE:
        return False
    if acct.is_active is False:
        return False
    return True


def clearing_account_id(db: Session, provider_name: str) -> int:
    """The provider's clearing account, created on demand (asset).

    A chosen `<p>_clearing_account_id` wins — but only if it still checks out
    (it exists, it's an active ordinary asset account, not bank-feed, not a
    control account); otherwise, and when none is chosen, the default asset
    account ("Stripe Clearing" / …) is found by name or created, keeping its
    suggested number only if the chart hasn't used it. Never raises: a bad id
    falls back rather than blocking the payment."""
    chosen = get_setting_raw(db, f"{provider_name}_clearing_account_id")
    if chosen:
        try:
            acct = db.query(Account).filter(Account.id == int(chosen)).first()
        except (TypeError, ValueError):
            acct = None
        if _is_valid_clearing_account(acct):
            return acct.id
        logger.warning(
            "Chosen %s clearing account (%r) is unusable; using the default",
            provider_name,
            chosen,
        )
    number, name = _CLEARING_ACCOUNTS[provider_name]
    # ensure_account find-or-creates by name, which isn't unique: two
    # concurrent first payments could both create it (or trip the number's
    # unique constraint). The window is tiny; left as is, like the 3900 helper.
    return ensure_account(
        db, number, name, AccountType.ASSET, detail_type="other_current_asset"
    ).id


def existing_clearing_account(db: Session, provider_name: str) -> Account | None:
    """The provider's clearing account if it already exists, creating nothing:
    the chosen account when it still checks out, else the default found by
    name, else None. For a read path (the payout suggestion, #584) that must
    not touch the chart. No clearing account means nothing has cleared yet, so
    there is nothing to pay out."""
    chosen = get_setting_raw(db, f"{provider_name}_clearing_account_id")
    if chosen:
        try:
            acct = db.query(Account).filter(Account.id == int(chosen)).first()
        except (TypeError, ValueError):
            acct = None
        if _is_valid_clearing_account(acct):
            return acct
    _, name = _CLEARING_ACCOUNTS[provider_name]
    return db.query(Account).filter(Account.name == name).first()


def existing_fee_account(db: Session, provider_name: str) -> Account | None:
    """The provider's fee account if it already exists, creating nothing (#584):
    the chosen account when valid, else the shared default by name, else None.
    A missing fee account is fine — the payout creates it with `fee_account_id`
    at post time, only when a fee is actually booked."""
    chosen = get_setting_raw(db, f"{provider_name}_fee_account_id")
    if chosen:
        try:
            acct = db.query(Account).filter(Account.id == int(chosen)).first()
        except (TypeError, ValueError):
            acct = None
        if _is_valid_fee_account(acct):
            return acct
    return db.query(Account).filter(Account.name == FEE_ACCOUNT_NAME).first()


def fee_account_id(db: Session, provider_name: str) -> int:
    """The processing-fee expense account, created on demand.

    A chosen `<p>_fee_account_id` wins — but only if it's still an active
    expense account; otherwise, and when none is chosen, the shared "Merchant
    Account Fees" expense account is found by name or created. Never raises."""
    chosen = get_setting_raw(db, f"{provider_name}_fee_account_id")
    if chosen:
        try:
            acct = db.query(Account).filter(Account.id == int(chosen)).first()
        except (TypeError, ValueError):
            acct = None
        if _is_valid_fee_account(acct):
            return acct.id
        logger.warning(
            "Chosen %s fee account (%r) is unusable; using the default",
            provider_name,
            chosen,
        )
    return ensure_account(
        db, FEE_ACCOUNT_NUMBER, FEE_ACCOUNT_NAME, AccountType.EXPENSE
    ).id
