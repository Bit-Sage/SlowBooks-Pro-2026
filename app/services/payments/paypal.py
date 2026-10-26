# ============================================================================
# PayPal provider — REST Checkout Orders v2 via httpx, no SDK.
#
# Flow: create order (intent=CAPTURE) → customer approves on PayPal's
# hosted page → capture. Approval does NOT capture by itself, so:
#   * poll_status captures an APPROVED order before reporting "paid"
#     (idempotent via the PayPal-Request-Id header). This is what makes
#     the return-redirect / desktop polling path record payments.
#   * The PAYMENT.CAPTURE.COMPLETED webhook is the belt-and-braces path
#     for server installs.
#
# Webhook verification is an API call (POST /v1/notification-webhooks/
# verify-webhook-signature) — unlike Stripe's local HMAC — so tests mock
# the transport, not the crypto.
#
# API base by environment: sandbox → api-m.sandbox.paypal.com,
# live → api-m.paypal.com. OAuth2 client-credentials bearer tokens are
# cached in-process (~9 h lifetime; cache keyed by client id so a
# credential change invalidates naturally).
# ============================================================================

import base64
import logging
import time
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
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}

# In-process token cache: {client_id: (access_token, expires_epoch)}
_token_cache: dict[str, tuple[str, float]] = {}


def api_base(settings: Mapping[str, str]) -> str:
    env = (settings.get("paypal_environment") or "sandbox").strip().lower()
    return _BASE_URLS.get(env, _BASE_URLS["sandbox"])


# ── Pure request builders (unit-testable without network) ────────────────


def build_token_request(settings: Mapping[str, str]) -> dict:
    creds = f"{settings['paypal_client_id']}:{settings['paypal_client_secret']}"
    basic = base64.b64encode(creds.encode()).decode()
    return {
        "method": "POST",
        "url": f"{api_base(settings)}/v1/oauth2/token",
        "headers": {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "data": {"grant_type": "client_credentials"},
    }


def build_order_request(
    invoice: Invoice, settings: Mapping[str, str], base_url: str, token: str
) -> dict:
    # what the payer sees on the hosted page; the ids and metadata
    # the webhook resolves by are untouched
    terms = terms_for(settings)
    return {
        "method": "POST",
        "url": f"{api_base(settings)}/v2/checkout/orders",
        "headers": {"Authorization": f"Bearer {token}"},
        "json": {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": "USD",
                        "value": str(_q(invoice.balance_due)),
                    },
                    "custom_id": str(invoice.id),
                    "invoice_id": invoice.invoice_number,
                    "description": document_reference(
                        document_label(invoice, terms), invoice.invoice_number
                    ),
                }
            ],
            "application_context": {
                "shipping_preference": "NO_SHIPPING",
                "user_action": "PAY_NOW",
                "return_url": f"{base_url}/pay/{invoice.payment_token}?status=success&provider=paypal",
                "cancel_url": f"{base_url}/pay/{invoice.payment_token}?status=cancelled&provider=paypal",
            },
        },
    }


def build_capture_request(
    order_id: str, settings: Mapping[str, str], token: str
) -> dict:
    return {
        "method": "POST",
        "url": f"{api_base(settings)}/v2/checkout/orders/{order_id}/capture",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # Idempotency: retrying the same capture (poll racing the
            # return redirect) must not double-charge.
            "PayPal-Request-Id": f"capture-{order_id}",
        },
        "json": {},
    }


def build_get_order_request(
    order_id: str, settings: Mapping[str, str], token: str
) -> dict:
    return {
        "method": "GET",
        "url": f"{api_base(settings)}/v2/checkout/orders/{order_id}",
        "headers": {"Authorization": f"Bearer {token}"},
    }


def build_verify_webhook_request(
    payload: bytes, headers: Mapping[str, str], settings: Mapping[str, str], token: str
) -> dict:
    import json as _json

    return {
        "method": "POST",
        "url": f"{api_base(settings)}/v1/notification-webhooks/verify-webhook-signature",
        "headers": {"Authorization": f"Bearer {token}"},
        "json": {
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "webhook_id": settings.get("paypal_webhook_id", ""),
            "webhook_event": _json.loads(payload.decode("utf-8")),
        },
    }


# ── Provider ─────────────────────────────────────────────────────────────


class PayPalProvider(PaymentProvider):
    name = "paypal"
    display_name = "PayPal"
    settings_keys = (
        "paypal_enabled",
        "paypal_environment",
        "paypal_client_id",
        "paypal_client_secret",
        "paypal_webhook_id",
        "paypal_clearing_enabled",
        "paypal_clearing_account_id",
        "paypal_fee_account_id",
    )
    secret_keys = ("paypal_client_secret",)

    def is_configured(self, settings: Mapping[str, str]) -> bool:
        return bool(
            settings.get("paypal_client_id") and settings.get("paypal_client_secret")
        )

    # -- auth ------------------------------------------------------------

    def _access_token(self, settings: Mapping[str, str]) -> str:
        client_id = settings["paypal_client_id"]
        cached = _token_cache.get(client_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        resp = _http.send(build_token_request(settings))
        if resp.status_code != 200:
            raise ValueError(f"PayPal auth failed (HTTP {resp.status_code})")
        data = resp.json()
        token = data["access_token"]
        _token_cache[client_id] = (token, time.time() + int(data.get("expires_in", 0)))
        return token

    # -- checkout --------------------------------------------------------

    def create_checkout(
        self, invoice: Invoice, settings: Mapping[str, str], base_url: str
    ) -> CheckoutSession:
        token = self._access_token(settings)
        resp = _http.send(build_order_request(invoice, settings, base_url, token))
        if resp.status_code not in (200, 201):
            logger.error(
                "PayPal order create failed: %s %s", resp.status_code, resp.text
            )
            raise ValueError(f"PayPal order creation failed (HTTP {resp.status_code})")
        order = resp.json()
        approve = next(
            (
                link["href"]
                for link in order.get("links", [])
                if link.get("rel") in ("approve", "payer-action")
            ),
            None,
        )
        if not approve:
            raise ValueError("PayPal order response carried no approval link")
        return CheckoutSession(url=approve, external_id=order["id"])

    # -- webhook ---------------------------------------------------------

    def verify_webhook(
        self, payload: bytes, headers: Mapping[str, str], settings: Mapping[str, str]
    ) -> Optional[PaymentResult]:
        if not settings.get("paypal_webhook_id"):
            raise ValueError("PayPal webhook id not configured")
        if not self.is_configured(settings):
            raise ValueError("PayPal is not configured")

        token = self._access_token(settings)
        try:
            request = build_verify_webhook_request(payload, headers, settings, token)
        except Exception as exc:
            raise ValueError("Invalid webhook payload") from exc
        resp = _http.send(request)
        if (
            resp.status_code != 200
            or resp.json().get("verification_status") != "SUCCESS"
        ):
            raise ValueError("Invalid webhook signature")

        event = request["json"]["webhook_event"]
        if event.get("event_type") != "PAYMENT.CAPTURE.COMPLETED":
            return None

        resource = event.get("resource") or {}
        amount = (resource.get("amount") or {}).get("value")
        custom_id = resource.get("custom_id")
        order_id = (
            ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get(
                "order_id"
            )
            or resource.get("id")
            or ""
        )
        fee = None
        if clearing_enabled(settings, self.name):
            # The capture resource carries the fee directly — no extra call.
            fee = _capture_fee(resource)
        return PaymentResult(
            status="paid",
            external_id=order_id,
            amount=Decimal(amount) if amount is not None else None,
            invoice_id=int(custom_id) if custom_id else None,
            fee=fee,
            raw=resource,
        )

    # -- polling ---------------------------------------------------------

    def poll_status(
        self, external_id: str, settings: Mapping[str, str]
    ) -> PaymentResult:
        token = self._access_token(settings)
        resp = _http.send(build_get_order_request(external_id, settings, token))
        if resp.status_code != 200:
            return PaymentResult(status="unknown", external_id=external_id)
        order = resp.json()
        status = order.get("status")

        if status == "APPROVED":
            # Approval isn't money — capture it now (idempotent via
            # PayPal-Request-Id). This is the return-redirect / desktop
            # recording path.
            cap = _http.send(build_capture_request(external_id, settings, token))
            if cap.status_code in (200, 201):
                order = cap.json()
                status = order.get("status")

        if status == "COMPLETED":
            fee = _order_fee(order) if clearing_enabled(settings, self.name) else None
            return PaymentResult(
                status="paid",
                external_id=external_id,
                amount=_order_captured_amount(order),
                invoice_id=_order_invoice_id(order),
                fee=fee,
                raw=order,
            )
        if status in ("VOIDED",):
            return PaymentResult(status="cancelled", external_id=external_id, raw=order)
        return PaymentResult(status="pending", external_id=external_id, raw=order)


def _order_invoice_id(order: dict) -> Optional[int]:
    units = order.get("purchase_units") or []
    if units and units[0].get("custom_id"):
        try:
            return int(units[0]["custom_id"])
        except (TypeError, ValueError):
            return None
    return None


def _capture_fee(capture: dict) -> Optional[Decimal]:
    """The PayPal fee on a single capture resource (webhook shape)."""
    breakdown = capture.get("seller_receivable_breakdown") or {}
    value = (breakdown.get("paypal_fee") or {}).get("value")
    try:
        return Decimal(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _order_fee(order: dict) -> Optional[Decimal]:
    """Sum the PayPal fee across a captured order's completed captures; None
    when no capture reports one."""
    total = Decimal("0")
    found = False
    for unit in order.get("purchase_units") or []:
        captures = ((unit.get("payments") or {}).get("captures")) or []
        for cap in captures:
            if cap.get("status") == "COMPLETED":
                fee = _capture_fee(cap)
                if fee is not None:
                    total += fee
                    found = True
    return total if found else None


def _order_captured_amount(order: dict) -> Optional[Decimal]:
    """Sum completed captures; fall back to the order amount."""
    units = order.get("purchase_units") or []
    total = Decimal("0")
    found = False
    for unit in units:
        captures = ((unit.get("payments") or {}).get("captures")) or []
        for cap in captures:
            if cap.get("status") == "COMPLETED":
                value = (cap.get("amount") or {}).get("value")
                if value is not None:
                    total += Decimal(value)
                    found = True
    if found:
        return total
    if units:
        value = (units[0].get("amount") or {}).get("value")
        if value is not None:
            return Decimal(value)
    return None
