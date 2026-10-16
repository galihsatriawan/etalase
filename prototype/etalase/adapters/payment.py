"""Payment adapters. Etalase does not receive buyer payments in its own bank account: money sits
with the payment provider, in the seller's sub-account, and Etalase decides when it is released.
Whether the provider can enforce that timing is unconfirmed (ADR 0003)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import timezone
from typing import Protocol

import httpx

from ..models import Order, PaymentInfo, new_id

log = logging.getLogger("etalase.payment")


class ManualActionRequired(RuntimeError):
    """The provider step is not automated for this account yet; operations finish it by hand."""


class PaymentProvider(Protocol):
    name: str

    def create_payment(self, order: Order, seller_account_id: str | None = None) -> PaymentInfo: ...
    def parse_webhook(self, headers: dict[str, str], body: dict) -> tuple[str, str] | None:
        """Return (payment_reference, status) or None if the event is not relevant."""
    def refund(self, order: Order, amount: int | None = None) -> None:
        """Give the buyer money back: the whole payment, or `amount` of it (a change-of-mind return keeps the shipping)."""
    def release_to_seller(self, order: Order, seller_account_id: str | None, amount: int, commission: int) -> str: ...


class MockPayment:
    """Test mode: fake QRIS strings; payments are confirmed through /dev endpoints or the demo."""

    name = "mock"

    def __init__(self) -> None:
        self.refunds: list[str] = []
        self.refund_amounts: dict[str, int] = {}
        self.releases: list[tuple[str, int, int]] = []

    def create_payment(self, order: Order, seller_account_id: str | None = None) -> PaymentInfo:
        ref = new_id("pay")
        # The QR payload must not reveal the payment reference: whoever knows the reference
        # could confirm the payment through the (token-protected) mock webhook.
        digest = hashlib.sha256(ref.encode()).hexdigest()[:16].upper()
        return PaymentInfo(provider=self.name, reference=ref, qr_string=f"00020101021226-MOCK-QRIS-{digest}-{order.total}")

    def parse_webhook(self, headers, body):
        if body.get("reference") and body.get("status"):
            return body["reference"], body["status"]
        return None

    def refund(self, order: Order, amount: int | None = None) -> None:
        self.refunds.append(order.id)
        self.refund_amounts[order.id] = order.total if amount is None else amount

    def release_to_seller(self, order, seller_account_id, amount, commission) -> str:
        self.releases.append((order.id, amount, commission))
        return new_id("rel")


class XenditPayment:
    """Dynamic QRIS through Xendit's QR Codes API (`POST /qr_codes`, api-version 2022-07-31).

    Checked against Xendit's help center and migration guide on 2026-10-15. Xendit now calls this per-channel endpoint
    "legacy" and recommends the v3 Payment Requests API, with no sunset date published. We stay on /qr_codes because its
    request and response are documented end to end; the v3 QRIS expiry field and webhook were not. Revisit when Xendit
    announces a date. The official 2022-07-31 reference page could not be fetched, so the webhook payload below comes
    from a real callback quoted in xendit-php issue 233 and the help-center articles. `make doctor` cannot test it;
    pay a test QR from the Xendit simulator and watch the order flip.

    Refunds and sub-account settlement (xenPlatform) depend on the account setup, so they are left explicit.
    """

    name = "xendit"
    BASE = "https://api.xendit.co"
    MAX_AMOUNT = 10_000_000  # QRIS limit per transaction in IDR (docs.xendit.co/docs/qris)
    max_amount = MAX_AMOUNT

    def __init__(self, secret_key: str, callback_token: str) -> None:
        if not callback_token:
            raise ValueError("XENDIT_CALLBACK_TOKEN is required")
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "api-version": "2022-07-31"}
        self.callback_token = callback_token

    @staticmethod
    def _expiry(value) -> str:
        """Xendit's examples all use UTC with a trailing Z: 2022-11-24T12:38:05.027203Z."""
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def create_payment(self, order: Order, seller_account_id: str | None = None) -> PaymentInfo:
        headers = dict(self.headers)
        if seller_account_id:  # xenPlatform: the payment lands in the seller's sub-account
            headers["for-user-id"] = seller_account_id
        body = {"reference_id": order.id, "type": "DYNAMIC", "currency": order.currency, "amount": order.total}
        if order.expires_at:  # the QR must die with the reservation
            body["expires_at"] = self._expiry(order.expires_at)
        resp = httpx.post(f"{self.BASE}/qr_codes", headers=headers, timeout=15, json=body)
        if resp.status_code >= 400:
            log.error("Xendit QR creation failed: HTTP %s %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        return PaymentInfo(provider=self.name, reference=order.id, qr_string=data.get("qr_string", ""),
                           provider_id=data.get("id", ""))

    def parse_webhook(self, headers, body):
        """Header x-callback-token must match. A paid QR arrives as event `qr.payment` with data.reference_id (the order
        id we sent) and data.status SUCCEEDED. Refund callbacks use the same URL and must not count as payments."""
        supplied = headers.get("x-callback-token") or ""
        if not self.callback_token or not hmac.compare_digest(supplied, self.callback_token):
            raise PermissionError("invalid Xendit callback token")
        event = body.get("event")
        if event and event != "qr.payment":
            return None
        data = body.get("data", body)
        ref = data.get("reference_id")
        status = (data.get("status") or "").upper()
        if ref and status in ("SUCCEEDED", "COMPLETED", "PAID"):
            return ref, "paid"
        return None

    def refund(self, order: Order, amount: int | None = None) -> None:
        # Xendit's own pages disagree on how long a QR payment can be refunded (7 or 30 days) and on partial refunds, and
        # an issuer that is not supported needs a disbursement instead. An operator decides per case until that is settled.
        raise ManualActionRequired(f"Xendit QR refund of Rp{order.total if amount is None else amount:,} is not automated "
                                   "yet (see docs/05); refund from the dashboard or send a disbursement.".replace(",", "."))

    def release_to_seller(self, order, seller_account_id, amount, commission) -> str:
        raise ManualActionRequired("xenPlatform settlement is not automated yet (see docs/05); release from the dashboard.")
