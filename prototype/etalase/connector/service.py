"""Connector API: lets OMSs, e-commerce plugins and local website builders push catalog and
stock to Etalase and receive orders by webhook. Integrate once, enable every store.

Auth: one API key per connection (header X-Etalase-Key); only its SHA-256 is stored.
Outgoing order webhooks are signed: X-Etalase-Signature = hex(HMAC-SHA256(secret, body)).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ipaddress
import secrets
import socket
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlparse

import httpx
from pydantic import AfterValidator, BaseModel, Field

from ..catalog import readiness
from ..models import Connection, Product, ProductStatus, StockOwner, Variant, now

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.connector")


def _https_url(value: str) -> str:
    u = urlparse(value)
    if u.scheme != "https" or not u.hostname or len(value) > 500:
        raise ValueError("must be an https URL (max 500 chars)")
    return value


HttpsUrl = Annotated[str, AfterValidator(_https_url)]


def check_webhook_url(url: str, allow_test: bool) -> None:
    """Order webhooks carry buyer addresses, so they may only go to public https endpoints
    (no internal services, no metadata server)."""
    if allow_test and url.startswith("test://"):
        return
    _https_url(url)
    host = urlparse(url).hostname
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, 443)}
    except socket.gaierror:
        raise ValueError("webhook host does not resolve") from None
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("webhook URL must point to a public address")


class VariantIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=64)
    options: dict[str, str] = Field(default_factory=dict, max_length=5)
    price: int = Field(ge=0, le=1_000_000_000)
    weight_gram: int = Field(default=0, ge=0, le=100_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)


class ProductIn(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="", max_length=80)
    images: list[HttpsUrl] = Field(default_factory=list, max_length=10)
    variants: list[VariantIn] = Field(min_length=1, max_length=100)


class StockIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    stock: int = Field(ge=0, le=1_000_000)


class OrderStatusIn(BaseModel):
    status: Literal["accepted", "rejected", "shipped"]
    waybill: str | None = Field(default=None, max_length=40)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class ConnectorService:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self.delivered: list[tuple[str, dict]] = []  # for tests and the demo

    def create_connection(self, seller_id: str, kind: str = "connector_api",
                          order_webhook_url: str | None = None) -> tuple[Connection, str]:
        if kind not in ("connector_api", "woocommerce", "jubelio", "ginee", "website_builder"):
            raise ValueError("unknown connection kind")
        if order_webhook_url:
            check_webhook_url(order_webhook_url, allow_test=self.ctx.settings.is_dev)
        key = "etk_" + secrets.token_urlsafe(24)
        conn = Connection(seller_id=seller_id, kind=kind, api_key_hash=hash_key(key),
                          order_webhook_url=order_webhook_url, webhook_secret=secrets.token_hex(16))
        self.ctx.store.put_connection(conn)
        seller = self.ctx.store.get_seller(seller_id)
        seller.stock_owner = StockOwner.OMS if kind in ("jubelio", "ginee") else StockOwner.STORE
        self.ctx.store.put_seller(seller)
        return conn, key

    def authenticate(self, key: str | None) -> Connection | None:
        return self.ctx.store.find_connection_by_key_hash(hash_key(key)) if key else None

    def upsert_product(self, conn: Connection, external_id: str, body: ProductIn) -> Product:
        store = self.ctx.store
        existing = next((p for p in store.list_products(seller_id=conn.seller_id) if p.external_ref == external_id), None)
        product = existing or Product(seller_id=conn.seller_id, external_ref=external_id, title=body.title)
        product.title, product.description = body.title, body.description
        product.category, product.images = body.category, body.images
        old = {v.external_ref: v for v in product.variants}
        product.variants = []
        stock_updates = []
        for vi in body.variants:
            existing_v = old.get(vi.external_id)
            v = existing_v or Variant(sku=vi.sku, external_ref=vi.external_id, price=vi.price)
            v.sku, v.options, v.price, v.weight_gram = vi.sku, vi.options, vi.price, vi.weight_gram
            if existing_v:  # stock of a live variant only moves through set_stock (keeps reservations intact)
                stock_updates.append((v.id, vi.stock))
            else:
                v.stock.on_hand, v.stock.last_confirmed_at, v.stock.source = vi.stock, now(), conn.kind
            product.variants.append(v)
        product.readiness_score, product.readiness_missing = readiness.score(product)
        product.status = ProductStatus.PUBLISHED if readiness.publishable(product) else ProductStatus.NEEDS_REVIEW
        store.put_product(product)
        for variant_id, qty in stock_updates:
            store.set_stock(conn.seller_id, product.id, variant_id, qty, conn.kind)
        return store.get_product(product.id)

    def update_stock(self, conn: Connection, items: list[StockIn]) -> dict:
        updated, unknown = 0, []
        by_sku = {v.sku: (p, v) for p in self.ctx.store.list_products(seller_id=conn.seller_id) for v in p.variants}
        for it in items:
            if it.sku not in by_sku:
                unknown.append(it.sku)
                continue
            p, v = by_sku[it.sku]
            change = self.ctx.store.set_stock(conn.seller_id, p.id, v.id, max(0, it.stock), conn.kind)
            self.ctx.events.publish("stock.changed", change.model_dump(mode="json"))
            updated += 1
        return {"updated": updated, "unknown_skus": unknown}

    def order_status(self, conn: Connection, order_id: str, body: OrderStatusIn):
        co = self.ctx.checkout
        if body.status == "accepted":
            return co.seller_accepts(conn.seller_id, order_id)
        if body.status == "rejected":
            return co.seller_rejects_paid(conn.seller_id, order_id)
        if body.status == "shipped":
            return co.mark_shipped(conn.seller_id, order_id, body.waybill)
        raise ValueError("status must be accepted, rejected or shipped")

    # ---- outgoing: paid orders go to the seller's store / OMS
    def deliver_order(self, payload: dict) -> None:
        from ..gateway.tools import order_view

        order = self.ctx.store.get_order(payload["order_id"])
        for conn in self.ctx.store.list_connections(order.seller_id):
            if not conn.order_webhook_url:
                continue
            body = {"event": "order.paid", "order": order_view(order),
                    "buyer": order.buyer.model_dump()}
            raw = json.dumps(body, separators=(",", ":")).encode()
            self.delivered.append((conn.order_webhook_url, body))
            if conn.order_webhook_url.startswith("test://"):
                continue
            try:
                check_webhook_url(conn.order_webhook_url, allow_test=False)  # DNS may have changed since
                httpx.post(conn.order_webhook_url, content=raw, timeout=10, follow_redirects=False, headers={
                    "content-type": "application/json", "X-Etalase-Signature": sign(conn.webhook_secret, raw)})
            except (httpx.HTTPError, ValueError):
                log.exception("order webhook to %s failed (retry via Pub/Sub in production)", conn.order_webhook_url)
