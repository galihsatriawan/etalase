"""UCP Order capability (dev.ucp.shopping.order): what the buyer's AI surface sees after the sale.

A platform that started a checkout with Etalase can read the resulting order (`GET /ucp/v1/orders/{id}`) and, when it is on the
allow-list, is also told about every change by a signed webhook, so its "My orders" screen can show the tracking number, the
delivery and a refund without asking again. The order is derived from Etalase's own order, so there is one source of truth:

    paid                   -> the order exists ("Order created" event, a fully populated order)
    seller accepted        -> fulfillment event `processing`
    shipped (with waybill) -> `shipped` with tracking_number, tracking_url, carrier
    delivered              -> `delivered`;  returned to sender / lost -> `returned_to_sender` / `undeliverable`
    refund (any cause)     -> an adjustment `refund` (or `cancellation` before shipping), negative totals, pending until the money moved
    return request         -> an adjustment `return`: pending, completed (refunded) or failed (lapsed or rejected)

Every webhook carries the whole current order, never a delta (order.md). Events and adjustments are append-only and have stable
ids, so a replay or a retry is the same event.

Who may receive webhooks
- Only platforms on the operator's allow-list (UCP_PLATFORMS). The platform's profile is fetched from the exact URL it sent in
  `UCP-Agent` when that URL is on the list, and its webhook URL must be on the platform's own host (or a host the list names).
- Every connection goes to an address that was resolved once, checked to be public, and then used for the request itself
  (the Host header and TLS name stay the platform's), so a DNS answer that changes between the check and the connection cannot
  point us at an internal address. Redirects are never followed, and bodies are size-capped.
- A webhook is signed with the key published in our profile (`signing_keys`): see ucp_signing.py.

Delivery
- At least once. Each delivery is stored first (an outbox), then sent; a transport error, a 5xx, 408, 425 or 429 is retried with
  growing delays (RETRY_AFTER), any other 4xx stops it. A retry is the same event: same Webhook-Id, Webhook-Timestamp and body.
- Deliveries for one order go out in order: a later one waits for an earlier one that is still being retried.
- A delivery that never succeeds ends as `failed` and raises `ops.manual_action` for an operator.

Test mode (UCP_TEST_MODE, sandbox only, refused outside dev with the mock adapters): accepts the conformance suite's mock card
token as payment, enables PUT /orders/{id} (a platform simulator adds events and adjustments) and
POST /testing/simulate-shipping/{id} behind a secret. None of this exists in production: a real order only changes through Etalase's
own order flow, and QRIS stays the only way to pay.

Not done: incoming request signatures are not verified (a platform proves who it is by the UCP-Agent profile it started the checkout
with, and an order id is unguessable); the order shows no buyer name or phone, only the delivery address.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from ..models import CheckoutError, Order, OrderStatus, now
from .ucp import ORDER, PAID_STATES, UCP_VERSION, UcpError, error_payload, to_minor
from .ucp_signing import Signer, SigningKeyError

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.ucp.order")

ORDER_KIND, DELIVERY_KIND, OUTBOX_KIND = "ucp_order", "ucp_delivery", "ucp_outbox"
OUTBOX_ID = "pending"
MOCK_HANDLER, MOCK_SUCCESS_TOKEN, MOCK_FAIL_TOKEN = "mock_payment_handler", "success_token", "fail_token"   # the suite's stand-in for a card
RETRY_AFTER = (2, 10, 60, 300, 1800, 7200, 21600, 43200)      # seconds to wait after attempt 1, 2, ... fails; then it is given up
RETRYABLE_4XX = {408, 425, 429}
CLAIM_SECONDS = 60
HTTP_TIMEOUT = 5.0
PROFILE_TTL = 300
MAX_PROFILE_BYTES = 200_000
MAX_NEW_ITEMS = 50
TRIGGERS = ("order.paid", "order.accepted", "order.rejected", "order.shipped", "order.delivered", "order.returned_to_sender",
            "order.parcel_lost", "return.requested", "return.approved", "return.disputed", "return.shipped_back",
            "return.refunded", "return.closed")


class DeliveryError(Exception):
    """A connection we refuse to make or could not make (not an HTTP answer)."""


# ------------------------------------------------------------------ platform allow-list
@dataclass(frozen=True)
class Platform:
    profile: str
    webhook_hosts: frozenset[str]

    @property
    def host(self) -> str:
        return (urlsplit(self.profile).hostname or "").lower()


def parse_platforms(raw: str) -> list[Platform]:
    """UCP_PLATFORMS: a JSON list; each entry is a profile URL or {"profile": url, "webhook_hosts": [host, ...]}."""
    if not (raw or "").strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"UCP_PLATFORMS is not valid JSON ({e})") from e
    if not isinstance(data, list):
        raise ValueError("UCP_PLATFORMS must be a JSON list")
    out = []
    for entry in data:
        profile, hosts = (entry, []) if isinstance(entry, str) else (entry.get("profile"), entry.get("webhook_hosts") or []) if isinstance(entry, dict) else (None, [])
        parts = urlsplit(profile) if isinstance(profile, str) else None
        if not parts or parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
            raise ValueError(f"UCP_PLATFORMS: {entry!r} is not a profile URL")
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
            raise ValueError("UCP_PLATFORMS: webhook_hosts must be a list of host names")
        out.append(Platform(profile.strip(), frozenset(h.lower() for h in hosts)))
    return out


def _is_public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified)


def _resolve(host: str, port: int) -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as e:
        raise DeliveryError(f"{host} does not resolve") from e


# ------------------------------------------------------------------ requests bodies of the order endpoints
class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class AdjustmentIn(_Open):
    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=40)
    occurred_at: AwareDatetime
    status: Literal["pending", "completed", "failed"]
    totals: list[dict] | None = Field(default=None, max_length=10)
    line_items: list[dict] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)


class EventIn(_Open):
    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=40)
    occurred_at: AwareDatetime
    line_items: list[dict] = Field(max_length=50)
    tracking_number: str | None = Field(default=None, max_length=64)
    tracking_url: str | None = Field(default=None, max_length=300)
    carrier: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=200)


class FulfillmentIn(_Open):
    events: list[EventIn] | None = None


class OrderIn(_Open):
    fulfillment: FulfillmentIn | None = None
    adjustments: list[AdjustmentIn] | None = None


def _iso(t: datetime) -> str:
    return t.isoformat()


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ------------------------------------------------------------------ the service
class UcpOrders:
    def __init__(self, ctx: "AppContext", transport: httpx.BaseTransport | None = None, resolver=None) -> None:
        self.ctx = ctx
        s = ctx.settings
        try:
            self.platforms = parse_platforms(s.ucp_platforms)
        except ValueError:
            log.exception("UCP_PLATFORMS is invalid: no platform receives order webhooks")
            self.platforms = []
        try:
            self.signer = Signer.load(s.ucp_signing_key)
        except SigningKeyError:
            log.exception("UCP_SIGNING_KEY is invalid: order webhooks are not sent")
            self.signer = None
        self.transport, self.resolver = transport, resolver or _resolve    # both replaceable, for tests
        self._profiles: dict[str, tuple[float, str | None]] = {}
        self._batch = threading.local()
        self._kick_lock = threading.Lock()

    # ---- what the profile says
    @property
    def test_mode(self) -> bool:
        return bool(self.ctx.settings.ucp_test_mode)

    def signing_keys(self) -> list[dict]:
        """The public key platforms verify webhooks with. Published when it is a real key, or in dev (where a throwaway one is made)."""
        if self.signer is None or not (self.signer.configured or self.ctx.settings.is_dev):
            return []
        return [self.signer.jwk()]

    def platform_for(self, profile_url: str) -> Platform | None:
        profile_url = (profile_url or "").strip()
        return next((p for p in self.platforms if p.profile == profile_url), None)

    # ---- connections: resolved once, checked, then used (DNS answers that change in between do not matter)
    def _send(self, method: str, url: str, headers: dict[str, str], content: bytes | None = None, limit: int | None = None):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme not in ("https", "http") or not host or parts.username or parts.password:
            raise DeliveryError("not a usable URL")
        extensions: dict[str, Any] = {}
        target, sent = url, dict(headers)
        if self.test_mode:                       # sandbox: a local mock platform on http://localhost is the point of the mode
            pass
        else:
            if parts.scheme != "https":
                raise DeliveryError("only https is used")
            port = parts.port or 443
            ips = self.resolver(host, port)
            if not ips or not all(_is_public(ip) for ip in ips):
                raise DeliveryError("the address is not public")
            ip = f"[{ips[0]}]" if ":" in ips[0] else ips[0]
            target = parts._replace(netloc=f"{ip}:{port}" if parts.port else ip).geturl()
            sent["Host"] = parts.netloc
            extensions["sni_hostname"] = host
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False, transport=self.transport) as client:
            with client.stream(method, target, headers=sent, content=content, extensions=extensions) as resp:
                body = b""
                if limit is not None:
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > limit:
                            raise DeliveryError("the answer is too large")
                return resp.status_code, body

    def webhook_url(self, profile_url: str) -> str | None:
        """The webhook URL a platform declared in its order capability, or None: not on the list, no profile, no valid URL."""
        platform = self.platform_for(profile_url)
        if platform is None:
            return None
        hit = self._profiles.get(platform.profile)
        if hit and hit[0] > now().timestamp():
            return hit[1]
        url = None
        try:
            status, body = self._send("GET", platform.profile, {"Accept": "application/json"}, limit=MAX_PROFILE_BYTES)
            if status == 200:
                url = self._webhook_from_profile(json.loads(body), platform)
            else:
                log.warning("platform profile %s answered %s", platform.profile, status)
        except (DeliveryError, httpx.HTTPError, ValueError) as e:
            log.warning("platform profile %s not usable: %s", platform.profile, e)
        self._profiles[platform.profile] = (now().timestamp() + (PROFILE_TTL if url else 60), url)
        return url

    def _webhook_from_profile(self, doc: Any, platform: Platform) -> str | None:
        caps = (doc.get("ucp") or {}).get("capabilities") if isinstance(doc, dict) else None
        entries = caps.get(ORDER) if isinstance(caps, dict) else next((c for c in caps or [] if isinstance(c, dict) and c.get("name") == ORDER), None)
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries or []:
            url = ((entry or {}).get("config") or {}).get("webhook_url") if isinstance(entry, dict) else None
            parts = urlsplit(url) if isinstance(url, str) and len(url) <= 500 else None
            if not parts or not parts.hostname or parts.username or parts.password:
                continue
            if parts.scheme != "https" and not (self.test_mode and parts.scheme == "http"):
                continue
            if parts.hostname.lower() == platform.host or parts.hostname.lower() in platform.webhook_hosts:
                return url
        return None

    # ---- the order record: what we told the platform, and everything the order needs that Etalase's own order does not hold
    def attach(self, order: Order, session: dict, profile_url: str) -> None:
        """Called when a UCP checkout creates the Etalase order. From now the platform may read it once it is paid."""
        record = {"order_id": order.id, "session_id": session["id"], "seller_id": order.seller_id, "origin": session.get("origin", ""),
                  "destination": session.get("destination") or {}, "profile": (profile_url or "")[:300],
                  "webhook_url": self.webhook_url(profile_url) if profile_url else None, "paid": False,
                  "events": [], "adjustments": [], "fp": "", "created_at": _iso(now())}
        self.ctx.store.create_doc(ORDER_KIND, order.id, record)

    def record(self, order_id: str) -> dict | None:
        return self.ctx.store.get_doc(ORDER_KIND, order_id[:64])

    @contextmanager
    def deferred(self):
        """Changes made inside are announced once, when the block ends (a shipment simulated as one step is one event)."""
        if getattr(self._batch, "ids", None) is not None:      # already inside one: the outer block announces
            yield
            return
        self._batch.ids = set()
        try:
            yield
        finally:
            ids, self._batch.ids = self._batch.ids, None
            for order_id in sorted(ids):
                self.sync(order_id)

    def on_event(self, payload: dict) -> None:
        order_id = payload.get("order_id")
        if not isinstance(order_id, str):
            return
        if getattr(self._batch, "ids", None) is not None:
            self._batch.ids.add(order_id)
            return
        self.sync(order_id)

    # ---- the order as the platform sees it
    def snapshot(self, order: Order, rec: dict) -> dict:
        cur = order.currency
        lines, ids = [], []
        events, adjustments = rec["events"], rec["adjustments"]
        shipped = any(e["type"] in ("shipped", "delivered") for e in events)
        cancelled = any(a["type"] == "cancellation" for a in adjustments)
        for n, item in enumerate(order.items, 1):
            total = to_minor(item.unit_price * item.qty, cur)
            qty = {"original": item.qty, "total": 0 if cancelled else item.qty, "fulfilled": item.qty if shipped and not cancelled else 0}
            status = "removed" if qty["total"] == 0 else "fulfilled" if qty["fulfilled"] == qty["total"] else "partial" if qty["fulfilled"] else "processing"
            lines.append({"id": f"li_{n}", "item": {"id": item.variant_id, "title": item.title, "price": to_minor(item.unit_price, cur)},
                          "quantity": qty, "totals": [{"type": "subtotal", "amount": total}, {"type": "total", "amount": total}],
                          "status": status})
            ids.append({"id": f"li_{n}", "quantity": item.qty})
        rate = order.shipping_rate
        title = f"{rate.courier.upper()} {rate.service}".strip() if rate else "Courier delivery"
        expectation: dict[str, Any] = {"id": "exp_1", "line_items": ids, "method_type": "shipping", "description": title,
                                       "destination": {k: v for k, v in rec["destination"].items() if k != "id" and v}}
        fulfillment: dict[str, Any] = {"expectations": [expectation]}
        if events:
            fulfillment["events"] = events
        out: dict[str, Any] = {
            "ucp": {"version": UCP_VERSION, "capabilities": {ORDER: [{"version": UCP_VERSION}]}},
            "id": order.id, "label": order.id, "checkout_id": rec["session_id"],
            "permalink_url": f"{rec['origin'] or self.ctx.settings.public_base_url.rstrip('/')}/pay/{order.id}",
            "line_items": lines, "fulfillment": fulfillment, "currency": cur,
            "totals": [{"type": "subtotal", "amount": to_minor(order.subtotal, cur)},
                       {"type": "fulfillment", "display_text": "Shipping", "amount": to_minor(order.shipping_fee, cur)},
                       {"type": "total", "amount": to_minor(order.total, cur)}]}
        if adjustments:
            out["adjustments"] = adjustments
        return out

    def _derive(self, order: Order, rec: dict) -> tuple[list[dict], list[dict]]:
        """The fulfillment events and adjustments Etalase's own order implies right now. Ids are stable; times are the real ones when the
        order has them and the moment we first noticed otherwise (merge keeps the first)."""
        origin = rec["origin"] or self.ctx.settings.public_base_url.rstrip("/")
        every = [{"id": f"li_{n}", "quantity": it.qty} for n, it in enumerate(order.items, 1)]
        ship, rate, at = order.shipment, order.shipping_rate, now()
        carrier = f"{rate.courier.upper()} {rate.service}".strip() if rate else ""
        events: list[dict] = []
        if ship is not None or order.status in (OrderStatus.ACCEPTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.PAID_OUT,
                                                OrderStatus.RETURN_REQUESTED, OrderStatus.RETURN_APPROVED, OrderStatus.RETURN_DISPUTED):
            events.append({"id": f"evt_{order.id}_processing", "type": "processing", "occurred_at": _iso(at), "line_items": every})
        track = {k: v for k, v in (("tracking_number", (ship.waybill or ship.tracking_id) if ship else ""),
                                   ("tracking_url", f"{origin}/pay/{order.id}"), ("carrier", carrier)) if v} if ship else {}
        if ship and ship.waybill:
            events.append({"id": f"evt_{order.id}_shipped", "type": "shipped", "occurred_at": _iso(ship.shipped_at), "line_items": every, **track})
        if ship and (order.delivered_at or ship.status == "delivered"):
            events.append({"id": f"evt_{order.id}_delivered", "type": "delivered", "occurred_at": _iso(order.delivered_at or at),
                           "line_items": every, **track})
        if ship and ship.status == "returned":
            events.append({"id": f"evt_{order.id}_returned", "type": "returned_to_sender", "occurred_at": _iso(at), "line_items": every, **track})
        if ship and ship.status in ("lost", "failed"):
            events.append({"id": f"evt_{order.id}_undeliverable", "type": "undeliverable", "occurred_at": _iso(at), "line_items": every, **track})

        adjustments: list[dict] = []
        rr = order.return_request
        if rr is not None:
            status = "completed" if rr.outcome == "refunded" else "failed" if rr.outcome in ("lapsed", "rejected") else "pending"
            adjustments.append({"id": f"adj_{order.id}_return", "type": "return", "occurred_at": _iso(rr.requested_at), "status": status,
                                "description": f"Return requested: {rr.reason.value.replace('_', ' ')}"})
        pay = order.payment
        if pay is not None and pay.status in ("refunded", "refund_pending"):
            done = pay.status == "refunded"
            amount = pay.refunded_amount if done else (rr.refund_amount if rr and rr.refund_amount else order.total)
            kind = "cancellation" if ship is None else "refund"
            adjustments.append({"id": f"adj_{order.id}_{kind}", "type": kind, "occurred_at": _iso(at),
                                "status": "completed" if done else "pending", "totals": [{"type": "total", "amount": -to_minor(amount, order.currency)}],
                                "description": self._refund_reason(order, kind)})
        return events, adjustments

    @staticmethod
    def _refund_reason(order: Order, kind: str) -> str:
        for line in reversed(order.history):
            if "refunded" in line or "refund required" in line:
                m = re.search(r"\(([^)]{1,80})\)", line)
                if m:
                    return m.group(1)[:1].upper() + m.group(1)[1:]
        return "Order cancelled and refunded" if kind == "cancellation" else "Refund"

    @staticmethod
    def _merge(kept: list[dict], wanted: list[dict]) -> None:
        """Append what is new; update what changed in place but keep its first `occurred_at`. Nothing is ever removed."""
        index = {x["id"]: x for x in kept}
        for item in wanted:
            old = index.get(item["id"])
            if old is None:
                kept.append(item)
            else:
                old.update({k: v for k, v in item.items() if k != "occurred_at"})

    def sync(self, order_id: str) -> None:
        """Bring the record in line with the Etalase order and, when that changed what the platform sees, queue one webhook."""
        order = self.ctx.store.get_order(order_id)
        if order is None or order.channel != "ucp":
            return
        outcome: dict[str, Any] = {}

        def change(rec: dict) -> bool:
            outcome.clear()      # Firestore may run a transaction callback more than once
            if not rec.get("paid"):
                if order.status not in PAID_STATES:
                    return False             # until the sale is made there is no order to show
                rec["paid"] = True
            events, adjustments = self._derive(order, rec)
            self._merge(rec["events"], events)
            self._merge(rec["adjustments"], adjustments)
            snap = self.snapshot(order, rec)
            fp = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
            if fp == rec.get("fp"):
                return False
            rec["fp"] = fp
            outcome["snap"], outcome["url"], outcome["profile"], outcome["origin"] = snap, rec.get("webhook_url"), rec["profile"], rec["origin"]
            return True
        self.ctx.store.update_doc(ORDER_KIND, order_id, change)
        if outcome.get("snap") and outcome.get("url"):
            self.enqueue(order_id, outcome["url"], outcome["snap"], outcome["origin"])

    def view(self, order_id: str, profile_url: str, seller_id: str | None) -> dict:
        """GET /orders/{id}. Only the platform that started the checkout reads its order, and only once it is paid."""
        rec = self.record(order_id)
        if rec is None or (seller_id is not None and rec["seller_id"] != seller_id) or not rec["profile"] or rec["profile"] != profile_url[:300]:
            raise UcpError(404, "not_found", "order not found")
        self.sync(order_id)
        rec = self.record(order_id)
        order = self.ctx.store.get_order(order_id)
        if rec is None or order is None or not rec.get("paid"):
            raise UcpError(404, "not_found", "order not found")
        return self.snapshot(order, rec)

    # ---- test mode (sandbox only)
    def _mock_tokens(self, raw: bytes) -> list[str]:
        """Test mode: the credential tokens of the suite's mock-handler instruments in a complete request body."""
        if not self.test_mode:
            return []
        try:
            instruments = (json.loads(raw or b"{}").get("payment") or {}).get("instruments") or []
        except (ValueError, AttributeError):
            return []
        return [i["credential"].get("token") for i in instruments
                if isinstance(i, dict) and i.get("handler_id") == MOCK_HANDLER and isinstance(i.get("credential"), dict)]

    def accepts_mock_payment(self, raw: bytes) -> bool:
        """Test mode: the suite's mock card token stands in for the buyer paying. Anything else is left to the normal QRIS flow."""
        return MOCK_SUCCESS_TOKEN in self._mock_tokens(raw)

    def declines_mock_payment(self, raw: bytes) -> bool:
        """Test mode: the suite's failing token (a declined card). The checkout answers payment_failed and nothing is reserved."""
        return MOCK_FAIL_TOKEN in self._mock_tokens(raw)

    def update_for_test(self, order_id: str, profile_url: str, seller_id: str | None, body: dict) -> dict:
        """PUT /orders/{id} (test mode): append the events and adjustments of the body that the order does not have yet."""
        if not self.test_mode:
            raise UcpError(404, "not_found", "no such endpoint")
        self.view(order_id, profile_url, seller_id)            # same access rule as a read, and the record is current
        try:
            parsed = OrderIn.model_validate(body)
        except ValidationError as e:
            detail = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
            raise UcpError(422, "invalid_request", detail) from e
        new_events = [e.model_dump(mode="json", exclude_none=True) for e in (parsed.fulfillment.events if parsed.fulfillment else None) or []]
        new_adjustments = [a.model_dump(mode="json", exclude_none=True) for a in parsed.adjustments or []]

        def add(rec: dict) -> bool:
            changed = False
            for key, items in (("events", new_events), ("adjustments", new_adjustments)):
                known = {x["id"] for x in rec[key]}
                fresh = [x for x in items if x["id"] not in known][:MAX_NEW_ITEMS]
                rec[key].extend(fresh)
                changed = changed or bool(fresh)
            return changed
        self.ctx.store.update_doc(ORDER_KIND, order_id, add)
        self.sync(order_id)
        return self.view(order_id, profile_url, seller_id)

    def simulate_shipping(self, order_id: str) -> None:
        """POST /testing/simulate-shipping/{id} (test mode): the seller accepts and ships, announced as one update."""
        order = self.ctx.store.get_order(order_id[:64])
        if not self.test_mode or order is None or order.channel != "ucp":
            raise UcpError(404, "not_found", "order not found")
        with self.deferred():
            if order.status == OrderStatus.PAID:
                order = self.ctx.checkout.seller_accepts(order.seller_id, order.id)
            try:
                self.ctx.checkout.mark_shipped(order.seller_id, order.id, "SIM" + secrets.token_hex(5).upper())
            except CheckoutError as e:
                raise UcpError(409, "not_shippable", str(e)[:200]) from e

    def secret_ok(self, given: str | None) -> bool:
        secret = self.ctx.settings.ucp_simulation_secret
        return bool(secret) and bool(given) and hmac.compare_digest(secret.encode(), given.encode())

    # ---- delivery: an outbox, signed requests, retries
    def enqueue(self, order_id: str, url: str, snap: dict, origin: str) -> None:
        delivery = {"id": "whk_" + secrets.token_hex(10), "order_id": order_id, "url": url, "origin": origin,
                    "webhook_id": str(uuid.uuid4()), "timestamp": int(now().timestamp()),
                    "body": json.dumps(snap, separators=(",", ":")), "attempts": 0, "status": "pending", "claimed_until": "",
                    "next_at": _iso(now()), "created_at": _iso(now()), "last_error": ""}
        store = self.ctx.store
        store.put_doc(DELIVERY_KIND, delivery["id"], delivery)
        store.create_doc(OUTBOX_KIND, OUTBOX_ID, {"ids": []})

        def add(box: dict) -> bool:
            if delivery["id"] in box["ids"]:
                return False
            box["ids"].append(delivery["id"])
            return True
        store.update_doc(OUTBOX_KIND, OUTBOX_ID, add)
        self.kick()

    def _unqueue(self, delivery_id: str) -> None:
        def remove(box: dict) -> bool:
            if delivery_id not in box["ids"]:
                return False
            box["ids"].remove(delivery_id)
            return True
        self.ctx.store.update_doc(OUTBOX_KIND, OUTBOX_ID, remove)

    def kick(self) -> None:
        """Send what is due, off the caller's thread: a slow platform must never slow a checkout."""
        if self._kick_lock.acquire(blocking=False):
            def run():
                try:
                    self.deliver_due()
                finally:
                    self._kick_lock.release()
            threading.Thread(target=run, daemon=True, name="ucp-webhooks").start()

    def pending(self) -> int:
        box = self.ctx.store.get_doc(OUTBOX_KIND, OUTBOX_ID)
        return len(box["ids"]) if box else 0

    def deliver_due(self, when: datetime | None = None) -> list[str]:
        """Try every delivery that is due. Returns the ids delivered."""
        when = when or now()
        box = self.ctx.store.get_doc(OUTBOX_KIND, OUTBOX_ID)
        docs = [d for d in (self.ctx.store.get_doc(DELIVERY_KIND, i) for i in (box["ids"] if box else [])) if d]
        done, blocked = [], set()
        for d in sorted(docs, key=lambda x: x["created_at"]):
            if d["status"] != "pending":
                self._unqueue(d["id"])        # finished, but a crash kept it listed
                continue
            if d["order_id"] in blocked:
                continue                      # an earlier delivery of this order is still being tried: keep the order
            blocked.add(d["order_id"])
            if _parse_time(d["next_at"]) > when:
                continue
            outcome = self._attempt(d["id"], when)
            if outcome == "delivered":
                done.append(d["id"])
            if outcome in ("delivered", "failed"):
                blocked.discard(d["order_id"])        # the next one for this order may go
        return done

    def _attempt(self, delivery_id: str, when: datetime) -> str:
        """One try. Returns delivered, retry (it will be tried again later), failed (given up) or skipped (not due, or taken)."""
        claimed: dict[str, Any] = {}

        def claim(d: dict) -> bool:
            claimed.clear()
            if d["status"] != "pending" or _parse_time(d["next_at"]) > when or (d["claimed_until"] and _parse_time(d["claimed_until"]) > when):
                return False
            d["claimed_until"] = _iso(when + timedelta(seconds=CLAIM_SECONDS))
            claimed.update(d)
            return True
        self.ctx.store.update_doc(DELIVERY_KIND, delivery_id, claim)
        if not claimed:
            return "skipped"
        error, permanent, ok = "", False, False
        try:
            status = self._post(claimed)
            if 200 <= status < 300:
                ok = True
            else:
                error = f"HTTP {status}"
                permanent = status < 500 and status not in RETRYABLE_4XX
        except (DeliveryError, httpx.HTTPError) as e:
            error = f"{type(e).__name__}: {e}"[:200]
        except Exception as e:                                  # a bug must not leave the delivery claimed forever
            log.exception("webhook delivery %s crashed", delivery_id)
            error = f"{type(e).__name__}: {e}"[:200]

        gave_up = {}

        def finish(d: dict) -> bool:
            gave_up.clear()
            d["attempts"] += 1
            d["claimed_until"], d["last_error"] = "", error
            if ok:
                d["status"] = "delivered"
            elif permanent or d["attempts"] > len(RETRY_AFTER):
                d["status"] = "failed"
                gave_up["yes"] = True
            else:
                d["next_at"] = _iso(now() + timedelta(seconds=RETRY_AFTER[d["attempts"] - 1]))
            return True
        final = self.ctx.store.update_doc(DELIVERY_KIND, delivery_id, finish)
        if final and final["status"] != "pending":
            self._unqueue(delivery_id)
        if gave_up:
            log.error("order webhook %s to %s given up: %s", delivery_id, claimed["url"], error)
            self.ctx.events.publish("ops.manual_action", {"order_id": claimed["order_id"], "action": "ucp_webhook"})
            return "failed"
        if not ok:
            log.warning("order webhook %s to %s failed (%s), will retry", delivery_id, claimed["url"], error)
            return "retry"
        return "delivered"

    def _post(self, d: dict) -> int:
        if self.signer is None:
            raise DeliveryError("no usable signing key")
        base = d["origin"] or self.ctx.settings.public_base_url.rstrip("/")
        body = d["body"].encode()
        headers = {"Content-Type": "application/json", "Webhook-Id": d["webhook_id"], "Webhook-Timestamp": str(d["timestamp"]),
                   "Idempotency-Key": d["webhook_id"], "UCP-Agent": f'profile="{base}/.well-known/ucp"'}
        headers.update(self.signer.sign("POST", d["url"], headers, body))      # signed again on each attempt: `created` is the send time
        status, _ = self._send("POST", d["url"], headers, content=body)
        return status


# ------------------------------------------------------------------ wiring
def add_test_routes(app, ctx: "AppContext") -> None:
    """POST /testing/simulate-shipping/{id}: the seller accepts and ships, behind the Simulation-Secret header. Test mode only."""
    @app.post("/testing/simulate-shipping/{order_id}", include_in_schema=False)
    async def simulate_shipping(request: Request, order_id: str):
        if not ctx.ucp_orders.secret_ok(request.headers.get("simulation-secret")):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            await asyncio.to_thread(ctx.ucp_orders.simulate_shipping, order_id)
        except UcpError as e:
            return JSONResponse(error_payload(e.code, e.content, e.severity), status_code=e.http)
        return {"status": "shipped"}


def register(ctx: "AppContext") -> UcpOrders:
    orders = UcpOrders(ctx)
    for topic in TRIGGERS:
        ctx.events.subscribe(topic, orders.on_event)
    ctx.ucp_orders = orders
    return orders
