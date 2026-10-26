# ============================================================================
# Payment provider abstraction — the seam every online-payment integration
# implements. Stripe is the reference implementation; PayPal and Square
# plug in beside it without touching the accounting path (recorder.py).
#
# Design rules:
#   - Providers are stateless singletons (registry in __init__.py), fed the
#     settings dict per call — same shape as the state_tax engine registry.
#   - create_checkout returns a hosted-page URL; we never touch card data.
#   - verify_webhook is the ONLY authentication a webhook request gets, so
#     an unverifiable payload must raise, never "best effort".
#   - poll_status exists because desktop installs (127.0.0.1) can never
#     receive a webhook; polling by the stored external id is their only
#     path to recording a payment.
# ============================================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar, Literal, Mapping, Optional

from app.models.invoices import Invoice


@dataclass
class CheckoutSession:
    url: str  # customer redirect URL (provider-hosted page)
    external_id: str  # provider session/order id — stored, used for idempotency


@dataclass
class PaymentResult:
    status: Literal["paid", "pending", "cancelled", "unknown"]
    external_id: str  # idempotency key (session id / order id)
    amount: Optional[Decimal] = None  # gross amount actually captured
    invoice_id: Optional[int] = None  # set when the provider payload carries it
    # The provider's processing fee for this capture, filled only when the
    # provider's clearing setting is on (#583). None when unknown — either
    # clearing is off, or the fetch failed, or the provider reports it late
    # (Square); the recorder then posts the gross only.
    fee: Optional[Decimal] = None
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    name: ClassVar[str]  # "stripe" | "paypal" | "square"
    display_name: ClassVar[str]
    settings_keys: ClassVar[tuple[str, ...]]  # all settings this provider reads
    secret_keys: ClassVar[tuple[str, ...]]  # subset that must be masked on GET

    def is_enabled(self, settings: Mapping[str, str]) -> bool:
        return settings.get(f"{self.name}_enabled") == "true"

    @abstractmethod
    def is_configured(self, settings: Mapping[str, str]) -> bool:
        """Are the credentials present (regardless of the enabled flag)?"""

    @abstractmethod
    def create_checkout(
        self, invoice: Invoice, settings: Mapping[str, str], base_url: str
    ) -> CheckoutSession:
        """Create a hosted checkout for the invoice's balance due."""

    @abstractmethod
    def verify_webhook(
        self, payload: bytes, headers: Mapping[str, str], settings: Mapping[str, str]
    ) -> Optional[PaymentResult]:
        """Verify a webhook delivery and translate it to a PaymentResult.

        Returns None for event types this provider deliberately ignores.
        Raises ValueError when the signature/payload cannot be verified —
        the route turns that into a 400 and records nothing.
        """

    @abstractmethod
    def poll_status(
        self, external_id: str, settings: Mapping[str, str]
    ) -> PaymentResult:
        """Query the provider for the current state of a checkout.

        The desktop-mode fallback: no webhook can reach 127.0.0.1, so the
        invoice page's "Check payment status" button lands here.
        """

    def refund(self, external_id: str, settings: Mapping[str, str]):
        raise NotImplementedError(
            f"{self.display_name} refunds are not supported yet; "
            "refund from the provider dashboard and record a credit memo."
        )
