"""Payment adapters. Etalase never holds funds: money sits with the payment provider,
in the seller's sub-account, and Etalase only decides when it is released."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
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
    def refund(self, order: Order) -> None: ...
    def release_to_seller(self, order: Order, seller_account_id: str | None, amount: int, commission: int) -> str: ...


class MockPayment:
    """Test mode: fake QRIS strings; payments are confirmed through /dev endpoints or the demo."""

    name = "mock"

    def __init__(self) -> None:
        self.refunds: list[str] = []
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

    def refund(self, order: Order) -> None:
        self.refunds.append(order.id)

    def release_to_seller(self, order, seller_account_id, amount, commission) -> str:
        self.releases.append((order.id, amount, commission))
        return new_id("rel")


class XenditPayment:
    """Dynamic QRIS via Xendit's QR Codes API.

    Verify endpoint names, the api-version header and webhook payloads against the
    current Xendit docs before going live. Refunds and sub-account settlement
    (xenPlatform) depend on the account setup, so they are left explicit.
    """

    name = "xendit"
    BASE = "https://api.xendit.co"

    def __init__(self, secret_key: str, callback_token: str) -> None:
        if not callback_token:
            raise ValueError("XENDIT_CALLBACK_TOKEN is required")
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "api-version": "2022-07-31"}
        self.callback_token = callback_token

    def create_payment(self, order: Order, seller_account_id: str | None = None) -> PaymentInfo:
        headers = dict(self.headers)
        if seller_account_id:  # xenPlatform: the payment lands in the seller's sub-account
            headers["for-user-id"] = seller_account_id
        body = {"reference_id": order.id, "type": "DYNAMIC", "currency": order.currency, "amount": order.total}
        if order.expires_at:  # the QR must die with the reservation
            body["expires_at"] = order.expires_at.isoformat()
        resp = httpx.post(f"{self.BASE}/qr_codes", headers=headers, timeout=15, json=body)
        resp.raise_for_status()
        data = resp.json()
        return PaymentInfo(provider=self.name, reference=order.id, qr_string=data.get("qr_string", ""))

    def parse_webhook(self, headers, body):
        supplied = headers.get("x-callback-token") or ""
        if not self.callback_token or not hmac.compare_digest(supplied, self.callback_token):
            raise PermissionError("invalid Xendit callback token")
        data = body.get("data", body)
        ref = data.get("reference_id")
        status = (data.get("status") or "").upper()
        if ref and status in ("SUCCEEDED", "COMPLETED", "PAID"):
            return ref, "paid"
        return None

    def refund(self, order: Order) -> None:
        raise ManualActionRequired("Xendit QR refunds are not automated yet (see docs/05); refund from the dashboard.")

    def release_to_seller(self, order, seller_account_id, amount, commission) -> str:
        raise ManualActionRequired("xenPlatform settlement is not automated yet (see docs/05); release from the dashboard.")
