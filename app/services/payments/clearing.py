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

from typing import Mapping

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.accounting import ensure_account
from app.services.settings_service import get_setting_raw

PROVIDER_NAMES = ("stripe", "paypal", "square")

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


def clearing_account_id(db: Session, provider_name: str) -> int:
    """The provider's clearing account, created on demand (asset).

    A chosen `<p>_clearing_account_id` wins; otherwise the default asset
    account ("Stripe Clearing" / …) is found by name or created, keeping its
    suggested number only if the chart hasn't used it."""
    chosen = get_setting_raw(db, f"{provider_name}_clearing_account_id")
    if chosen:
        return int(chosen)
    number, name = _CLEARING_ACCOUNTS[provider_name]
    return ensure_account(
        db, number, name, AccountType.ASSET, detail_type="other_current_asset"
    ).id


def fee_account_id(db: Session, provider_name: str) -> int:
    """The processing-fee expense account, created on demand.

    A chosen `<p>_fee_account_id` wins; otherwise the shared "Merchant Account
    Fees" expense account is found by name or created."""
    chosen = get_setting_raw(db, f"{provider_name}_fee_account_id")
    if chosen:
        return int(chosen)
    return ensure_account(
        db, FEE_ACCOUNT_NUMBER, FEE_ACCOUNT_NAME, AccountType.EXPENSE
    ).id
