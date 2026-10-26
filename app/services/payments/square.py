# ============================================================================
# Square provider — Payment Links (quick pay) via httpx, no SDK.
#
# Flow: create a payment link for the invoice's balance → customer pays
# on Square's hosted page → the payment.updated webhook (or a poll of
# the order) reports COMPLETED and the shared recorder posts it.
#
# Two Square-specific wrinkles:
#   * Quick-pay links carry NO metadata passthrough, so the invoice is
#     resolved by the ORDER id we stored at checkout time
#     (Invoice.checkout_external_id, indexed for exactly this lookup) —
#     PaymentResult.invoice_id stays None and the webhook route does the
#     lookup.
#   * Webhook signatures HMAC over (notification_url + raw_body) with
#     the EXACT URL string configured in the Square dashboard. Behind a
#     proxy the request-derived URL can differ, so the
#     square_notification_url setting overrides it when set.
#
# API base by environment: sandbox → connect.squareupsandbox.com,
# production → connect.squareup.com. Square-Version is pinned so the
# response shapes can't drift under us.
# ============================================================================

import base64
import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from typing import Mapping, Optional

from app.models.invoices import Invoice
from app.services.accounting import _q
from app.services.payments import _http
from app.services.payments.base import CheckoutSession, PaymentProvider, PaymentResult
from app.services.payments.clearing import clearing_enabled
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_for

logger = logging.getLogger(__name__)

_BASE_URLS = {
    "sandbox": "https://connect.squareupsandbox.com",
    "production": "https://connect.squareup.com",
}

SQUARE_VERSION = "2026-06-18"


def api_base(settings: Mapping[str, str]) -> str:
    env = (settings.get("square_environment") or "sandbox").strip().lower()
    return _BASE_URLS.get(env, _BASE_URLS["sandbox"])


def _auth_headers(settings: Mapping[str, str]) -> dict:
    return {
        "Authorization": f"Bearer {settings['square_access_token']}",
        "Square-Version": SQUARE_VERSION,
    }


# ── Pure request builders (unit-testable without network) ────────────────


def build_payment_link_request(
    invoice: Invoice,
    settings: Mapping[str, str],
    base_url: str,
    idempotency_key: Optional[str] = None,
) -> dict:
    # what the payer sees on the hosted page; the ids and metadata
    # the webhook resolves by are untouched
    terms = terms_for(settings)
    amount_cents = int(_q(invoice.balance_due) * 100)
    return {
        "method": "POST",
        "url": f"{api_base(settings)}/v2/online-checkout/payment-links",
        "headers": _auth_headers(settings),
        "json": {
            "idempotency_key": idempotency_key or f"inv-{invoice.id}-{uuid.uuid4()}",
            "quick_pay": {
                "name": document_reference(
                    document_label(invoice, terms), invoice.invoice_number
                ),
                "price_money": {"amount": amount_cents, "currency": "USD"},
                "location_id": settings["square_location_id"],
            },
            "checkout_options": {
                "redirect_url": (
                    f"{base_url}/pay/{invoice.payment_token}"
                    f"?status=success&provider=square"
                ),
            },
            "payment_note": document_reference(
                document_label(invoice, terms), invoice.invoice_number
            ),
        },
    }


def build_get_order_request(order_id: str, settings: Mapping[str, str]) -> dict:
    return {
        "method": "GET",
        "url": f"{api_base(settings)}/v2/orders/{order_id}",
        "headers": _auth_headers(settings),
    }


def build_get_payment_request(payment_id: str, settings: Mapping[str, str]) -> dict:
    return {
        "method": "GET",
        "url": f"{api_base(settings)}/v2/payments/{payment_id}",
        "headers": _auth_headers(settings),
    }


def processing_fee(payment: dict) -> Optional[Decimal]:
    """Sum a Square payment's processing_fee[].amount_money (cents) to a
    dollar Decimal; None when the fee isn't reported yet (Square can add it
    after the payment completes)."""
    fees = payment.get("processing_fee")
    if not fees:
        return None
    total_cents = 0
    found = False
    for entry in fees:
        amount = (entry.get("amount_money") or {}).get("amount")
        if amount is not None:
            total_cents += amount
            found = True
    return Decimal(total_cents) / Decimal("100") if found else None


def webhook_signature(signature_key: str, notification_url: str, body: bytes) -> str:
    """Square's webhook signature: base64(HMAC-SHA256(key, url + body))."""
    mac = hmac.new(
        signature_key.encode(), notification_url.encode() + body, hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode()


# ── Provider ─────────────────────────────────────────────────────────────


class SquareProvider(PaymentProvider):
    name = "square"
    display_name = "Square"
    settings_keys = (
        "square_enabled",
        "square_environment",
        "square_access_token",
        "square_location_id",
        "square_webhook_signature_key",
        "square_notification_url",
        "square_clearing_enabled",
        "square_clearing_account_id",
        "square_fee_account_id",
    )
    secret_keys = ("square_access_token", "square_webhook_signature_key")

    def is_configured(self, settings: Mapping[str, str]) -> bool:
        return bool(
            settings.get("square_access_token") and settings.get("square_location_id")
        )

    # -- checkout --------------------------------------------------------

    def create_checkout(
        self, invoice: Invoice, settings: Mapping[str, str], base_url: str
    ) -> CheckoutSession:
        resp = _http.send(build_payment_link_request(invoice, settings, base_url))
        if resp.status_code not in (200, 201):
            logger.error(
                "Square payment link failed: %s %s", resp.status_code, resp.text
            )
            raise ValueError(
                f"Square payment link creation failed (HTTP {resp.status_code})"
            )
        link = resp.json().get("payment_link") or {}
        url = link.get("url")
        # The ORDER id (not the link id) is the key webhooks and polls
        # report against — it's what we store on the invoice.
        order_id = link.get("order_id")
        if not url or not order_id:
            raise ValueError("Square payment link response missing url/order_id")
        return CheckoutSession(url=url, external_id=order_id)

    # -- webhook ---------------------------------------------------------

    def verify_webhook(
        self, payload: bytes, headers: Mapping[str, str], settings: Mapping[str, str]
    ) -> Optional[PaymentResult]:
        signature_key = settings.get("square_webhook_signature_key", "")
        if not signature_key:
            raise ValueError("Square webhook signature key not configured")
        notification_url = settings.get("square_notification_url", "").strip()
        if not notification_url:
            raise ValueError(
                "square_notification_url not configured — Square signs over the "
                "exact webhook URL, so it must be set to the URL registered in "
                "the Square dashboard"
            )

        provided = headers.get("x-square-hmacsha256-signature", "")
        expected = webhook_signature(signature_key, notification_url, payload)
        if not provided or not hmac.compare_digest(expected, provided):
            raise ValueError("Invalid webhook signature")

        import json

        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid webhook payload") from exc

        if event.get("type") != "payment.updated":
            return None
        payment = ((event.get("data") or {}).get("object") or {}).get("payment") or {}
        if payment.get("status") != "COMPLETED":
            return None
        order_id = payment.get("order_id")
        if not order_id:
            return None
        amount_cents = (payment.get("amount_money") or {}).get("amount")
        fee = None
        if clearing_enabled(settings, self.name):
            # The webhook payment object carries processing_fee once Square
            # has settled it; absent (reported late) leaves fee None.
            try:
                fee = processing_fee(payment)
            except Exception:
                fee = None
        return PaymentResult(
            status="paid",
            external_id=order_id,
            amount=(
                Decimal(amount_cents) / Decimal("100")
                if amount_cents is not None
                else None
            ),
            invoice_id=None,  # resolved by checkout_external_id lookup
            fee=fee,
            raw=payment,
        )

    # -- polling ---------------------------------------------------------

    def poll_status(
        self, external_id: str, settings: Mapping[str, str]
    ) -> PaymentResult:
        resp = _http.send(build_get_order_request(external_id, settings))
        if resp.status_code != 200:
            return PaymentResult(status="unknown", external_id=external_id)
        order = resp.json().get("order") or {}
        state = order.get("state")
        if state == "CANCELED":
            return PaymentResult(status="cancelled", external_id=external_id, raw=order)
        if _order_is_paid(order):
            amount_cents = (order.get("total_money") or {}).get("amount")
            fee = None
            if clearing_enabled(settings, self.name):
                fee = self._poll_fee(order, settings)
            return PaymentResult(
                status="paid",
                external_id=external_id,
                amount=(
                    Decimal(amount_cents) / Decimal("100")
                    if amount_cents is not None
                    else None
                ),
                invoice_id=None,
                fee=fee,
                raw=order,
            )
        return PaymentResult(status="pending", external_id=external_id, raw=order)

    # -- fee -------------------------------------------------------------

    def _poll_fee(self, order: dict, settings: Mapping[str, str]) -> Optional[Decimal]:
        """Square's fee isn't on the order — the order's tender names a
        payment id, and the payment carries processing_fee. One extra GET;
        any failure returns None and never blocks recording the payment."""
        try:
            tenders = order.get("tenders") or []
            payment_id = next(
                (t.get("payment_id") or t.get("id") for t in tenders if t), None
            )
            if not payment_id:
                return None
            resp = _http.send(build_get_payment_request(payment_id, settings))
            if resp.status_code != 200:
                return None
            payment = resp.json().get("payment") or {}
            return processing_fee(payment)
        except Exception:
            logger.warning(
                "Square fee lookup failed; recording gross only", exc_info=True
            )
            return None


def _order_is_paid(order: dict) -> bool:
    """A payment-link order is paid when COMPLETED — but verified against
    the live sandbox (2026-08-01), a fully-paid quick-pay order stays in
    state OPEN with a tender attached and net_amount_due_money of 0, and
    never transitions to COMPLETED (that state is about fulfillment, not
    payment). Treat both shapes as paid; an OPEN order with no tenders or
    a remaining balance stays pending.
    """
    if order.get("state") == "COMPLETED":
        return True
    if order.get("state") != "OPEN":
        return False
    if not order.get("tenders"):
        return False
    net_due = (order.get("net_amount_due_money") or {}).get("amount")
    return net_due == 0
