"""Shipping adapters: courier rates, bookings and status updates."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..models import Order, Shipment, ShippingRate, new_id

# Internal statuses. Every provider maps its own status names onto these, because
# DELIVERED is what releases the seller's payout.
STATUSES = ("booked", "picked_up", "in_transit", "delivered", "returned", "failed")


class ShippingProvider(Protocol):
    name: str

    def get_rates(self, origin_postal: str, dest_postal: str, weight_gram: int, value: int) -> list[ShippingRate]: ...
    def book(self, order: Order, origin_name: str, origin_phone: str, origin_address: str,
             origin_postal: str = "") -> Shipment: ...
    def parse_webhook(self, body: dict) -> tuple[str, str] | None:
        """Return (waybill_or_order_id, internal_status)."""
    def confirm_delivered(self, order: Order) -> bool:
        """Ask the courier directly before money moves. Webhooks alone are never trusted for payouts."""
    def confirm_returned(self, order: Order) -> bool:
        """Same for a refund: the parcel is really back with the seller, not just on its way."""


def _postal(code: str) -> int:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not digits:
        raise ValueError("postal code is required")
    return int(digits)


class MockShipping:
    name = "mock"

    def get_rates(self, origin_postal, dest_postal, weight_gram, value):
        kg = max(1, -(-weight_gram // 1000))  # round up to whole kg
        return [
            ShippingRate(courier="jne", service="REG", price=12000 * kg, etd="2-3 days"),
            ShippingRate(courier="sicepat", service="REG", price=12500 * kg, etd="2-3 days"),
            ShippingRate(courier="jnt", service="EZ", price=13000 * kg, etd="2-4 days"),
        ]

    def book(self, order, origin_name, origin_phone, origin_address, origin_postal=""):
        rate = order.shipping_rate
        return Shipment(courier=rate.courier, service=rate.service, waybill=new_id("WB").upper())

    def parse_webhook(self, body):
        if body.get("waybill") and body.get("status") in STATUSES:
            return body["waybill"], body["status"]
        return None

    def confirm_delivered(self, order):
        return True  # simulation only; prod refuses the mock adapter

    def confirm_returned(self, order):
        return True


class BiteshipShipping:
    """Biteship aggregator (https://biteship.com/docs/api). Checked against the official docs on 2026-10-15; the
    contract is pinned by tests/test_biteship.py with recorded-shape fakes, not by live calls.

    Keys: `biteship_test.…` (sandbox: orders are simulated and free, but rates and public tracking are billed per hit
    and need a topped-up balance) or `biteship_live.…` (the Order API stays off until Biteship activates the account).
    """

    name = "biteship"
    BASE = "https://api.biteship.com"
    # Webhook / tracking statuses -> our internal statuses (docs: help.biteship.com "Courier Status Availability").
    STATUS_MAP = {"confirmed": "booked", "scheduled": "booked", "allocated": "booked", "picking_up": "booked",
                  "picked": "picked_up", "dropping_off": "in_transit", "in_transit": "in_transit", "on_hold": "in_transit",
                  "delivered": "delivered", "return_in_transit": "in_transit", "returned": "returned",
                  "cancelled": "failed", "rejected": "failed", "courier_not_found": "failed", "disposed": "failed"}

    def __init__(self, api_key: str, couriers: str = "jne,sicepat,jnt,anteraja") -> None:
        self.headers = {"authorization": api_key, "content-type": "application/json"}
        self.couriers = couriers

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        """Biteship errors are {"success": false, "error": "...", "code": 4000xxxx}; keep the text, drop nothing useful."""
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = f"{body.get('error') or body.get('message') or resp.text} (code {body.get('code', resp.status_code)})"
            except ValueError:
                detail = resp.text[:200]
            raise RuntimeError(f"Biteship HTTP {resp.status_code}: {detail}")

    def get_rates(self, origin_postal, dest_postal, weight_gram, value):
        if not (str(origin_postal).isdigit() and str(dest_postal).isdigit()):
            return []
        resp = httpx.post(f"{self.BASE}/v1/rates/couriers", headers=self.headers, timeout=15, json={
            "origin_postal_code": _postal(origin_postal), "destination_postal_code": _postal(dest_postal),
            "couriers": self.couriers,
            "items": [{"name": "parcel", "value": value, "quantity": 1, "weight": weight_gram}],
        })
        self._check(resp)
        return [ShippingRate(courier=r.get("courier_code", ""), service=r.get("courier_service_code", ""),
                             price=int(r.get("price", 0)), etd=str(r.get("duration", "")))
                for r in resp.json().get("pricing", [])]

    def book(self, order, origin_name, origin_phone, origin_address, origin_postal=""):
        rate = order.shipping_rate
        resp = httpx.post(f"{self.BASE}/v1/orders", headers=self.headers, timeout=20, json={
            "origin_contact_name": origin_name, "origin_contact_phone": origin_phone,
            "origin_address": origin_address, "origin_postal_code": _postal(origin_postal),   # one of postal/coordinate/area id is required
            "destination_contact_name": order.buyer.name, "destination_contact_phone": order.buyer.phone,
            "destination_address": order.buyer.line1, "destination_postal_code": _postal(order.buyer.postal_code),
            "courier_company": rate.courier, "courier_type": rate.service,   # exactly as the rates call returned them
            "delivery_type": "now",
            "reference_id": order.id,   # a retry with the same reference_id is rejected as a duplicate (code 40002060)
            "items": [{"name": i.title, "value": i.unit_price, "quantity": i.qty, "weight": max(1, i.weight_gram)}
                      for i in order.items],
        })
        self._check(resp)
        data = resp.json()
        courier = data.get("courier") or {}
        # The waybill can be empty when the courier issues it later; webhooks and delivery checks use the ids instead.
        return Shipment(courier=rate.courier, service=rate.service, waybill=courier.get("waybill_id") or "",
                        status="booked", provider_order_id=data.get("id") or "", tracking_id=courier.get("tracking_id") or "")

    def parse_webhook(self, body):
        """order.status events carry order_id (Biteship's id, there is no reference_id), courier_tracking_id,
        courier_waybill_id and status. order.price and order.waybill_id are acknowledged and ignored."""
        if body.get("event") not in (None, "order.status"):
            return None
        status = self.STATUS_MAP.get(str(body.get("status", "")).lower())
        key = body.get("order_id") or body.get("courier_tracking_id") or body.get("courier_waybill_id")
        return (key, status) if key and status else None

    def confirm_delivered(self, order):
        """Ask Biteship before money moves, so a forged webhook or a made-up waybill typed by the seller cannot release
        a payout. Orders booked through the Order API are checked by tracking id; a waybill the seller typed by hand
        is checked through public tracking by waybill and courier."""
        return self._tracking_status(order) == "delivered"

    def confirm_returned(self, order):
        """A refund is money too: only the final `returned` status counts, not `return_in_transit`."""
        return self._tracking_status(order) == "returned"

    def _tracking_status(self, order) -> str:
        s = order.shipment
        if not s:
            return ""
        if s.tracking_id:
            url = f"{self.BASE}/v1/trackings/{s.tracking_id}"
        elif s.waybill:
            url = f"{self.BASE}/v1/trackings/{s.waybill}/couriers/{s.courier}"
        else:
            return ""
        try:
            resp = httpx.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError:
            return ""
        return str(resp.json().get("status", "")).lower()
