"""Shipping adapters: courier rates, bookings and status updates."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..models import Order, Shipment, ShippingRate, new_id

# Internal statuses. Every provider maps its own status names onto these, because
# DELIVERED is what releases the seller's payout.
STATUSES = ("booked", "picked_up", "in_transit", "delivered", "returned")


class ShippingProvider(Protocol):
    name: str

    def get_rates(self, origin_postal: str, dest_postal: str, weight_gram: int, value: int) -> list[ShippingRate]: ...
    def book(self, order: Order, origin_name: str, origin_phone: str, origin_address: str) -> Shipment: ...
    def parse_webhook(self, body: dict) -> tuple[str, str] | None:
        """Return (waybill_or_order_id, internal_status)."""
    def confirm_delivered(self, order: Order) -> bool:
        """Ask the courier directly before money moves. Webhooks alone are never trusted for payouts."""


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

    def book(self, order, origin_name, origin_phone, origin_address):
        rate = order.shipping_rate
        return Shipment(courier=rate.courier, service=rate.service, waybill=new_id("WB").upper())

    def parse_webhook(self, body):
        if body.get("waybill") and body.get("status") in STATUSES:
            return body["waybill"], body["status"]
        return None

    def confirm_delivered(self, order):
        return True  # simulation only; prod refuses the mock adapter


class BiteshipShipping:
    """Biteship aggregator (30+ Indonesian couriers). Check field names against the API docs."""

    name = "biteship"
    BASE = "https://api.biteship.com"
    STATUS_MAP = {"picked": "picked_up", "picking_up": "booked", "dropping_off": "in_transit",
                  "on_hold": "in_transit", "delivered": "delivered", "returned": "returned"}

    def __init__(self, api_key: str, couriers: str = "jne,sicepat,jnt,anteraja") -> None:
        self.headers = {"authorization": api_key, "content-type": "application/json"}
        self.couriers = couriers

    def get_rates(self, origin_postal, dest_postal, weight_gram, value):
        if not (str(origin_postal).isdigit() and str(dest_postal).isdigit()):
            return []
        resp = httpx.post(f"{self.BASE}/v1/rates/couriers", headers=self.headers, timeout=15, json={
            "origin_postal_code": _postal(origin_postal), "destination_postal_code": _postal(dest_postal),
            "couriers": self.couriers,
            "items": [{"name": "parcel", "value": value, "quantity": 1, "weight": weight_gram}],
        })
        resp.raise_for_status()
        return [ShippingRate(courier=r.get("courier_code", ""), service=r.get("courier_service_code", ""),
                             price=int(r.get("price", 0)), etd=str(r.get("duration", "")))
                for r in resp.json().get("pricing", [])]

    def book(self, order, origin_name, origin_phone, origin_address):
        rate = order.shipping_rate
        resp = httpx.post(f"{self.BASE}/v1/orders", headers=self.headers, timeout=20, json={
            "origin_contact_name": origin_name, "origin_contact_phone": origin_phone,
            "origin_address": origin_address,
            "destination_contact_name": order.buyer.name, "destination_contact_phone": order.buyer.phone,
            "destination_address": order.buyer.line1, "destination_postal_code": _postal(order.buyer.postal_code),
            "courier_company": rate.courier, "courier_type": rate.service.lower(),
            "delivery_type": "now",
            "items": [{"name": i.title, "value": i.unit_price, "quantity": i.qty, "weight": 0} for i in order.items],
            "reference_id": order.id,
        })
        resp.raise_for_status()
        data = resp.json()
        return Shipment(courier=rate.courier, service=rate.service,
                        waybill=(data.get("courier") or {}).get("waybill_id", ""), status="booked")

    def parse_webhook(self, body):
        status = self.STATUS_MAP.get(str(body.get("status", "")).lower())
        key = body.get("courier_waybill_id") or body.get("order_id")
        return (key, status) if key and status else None

    def confirm_delivered(self, order):
        """Public tracking lookup by waybill and courier, so a forged webhook or a fake waybill
        typed by the seller cannot release a payout."""
        s = order.shipment
        if not s or not s.waybill:
            return False
        try:
            resp = httpx.get(f"{self.BASE}/v1/trackings/{s.waybill}/couriers/{s.courier}",
                             headers=self.headers, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError:
            return False
        return str(resp.json().get("status", "")).lower() == "delivered"
