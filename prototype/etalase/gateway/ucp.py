"""Universal Commerce Protocol (UCP): Etalase as a UCP business.

UCP (https://ucp.dev) is the open standard that lets AI shopping platforms discover a business and run a
checkout with it. This module implements the business side of:

- discovery profile at /.well-known/ucp, on the platform host and on every shop's own address (rina.<domain>), where it
  describes that shop alone: its endpoint is the shop's origin and its checkout only accepts that shop's items
- dev.ucp.shopping.checkout   over REST  (create / get / update / complete / cancel)
- dev.ucp.shopping.fulfillment extension (shipping address + courier choice)

How Etalase maps onto it
- item.id is a variant id (what /agent/v1 and the MCP tools return). A session holds up to MAX_LINES line items, all
  from one shop: a cart that mixes shops is refused, because one order belongs to one seller. The same variant sent
  twice is merged into one line. On a shop's own address only that shop's items exist.
- A session is just a draft (buyer, address, courier). Stock is only reserved on `complete`, because that is
  the point where an Etalase checkout (and its QRIS payment) is created. All lines are reserved together or not at all.
- Courier quotes are for the whole cart (summed weight and value), the same way the order will be priced.
- Payment is QRIS and must be finished by the buyer in a trusted UI. So once `complete` has reserved the stock and
  created the payment, the session is `requires_escalation` with a `requires_buyer_review` message and a
  `continue_url` to the hosted payment page (the spec obliges the platform to hand the buyer over there). If the
  seller confirms stock first, the session is `complete_in_progress` until the payment code exists. Polling GET
  returns `completed` (with `order`) once the payment webhook has landed, or `canceled` if the checkout expired
  or was rejected.
- Amounts are minor units per ISO 4217. IDR has exponent 2, so Rp 85.000 is sent as 8500000.

Not implemented (and therefore not advertised): AP2 mandates, payment handlers, discounts, cart, catalog and
order capabilities, message signing, pickup, carts of more than one shop. The platform's own profile (UCP-Agent) is
not fetched, so capability negotiation is simply "we return what we implement" (fetching an arbitrary URL
from a request header is an SSRF risk; add an allow-listed fetch before relying on negotiation).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import attribution
from ..models import Address, CheckoutError, OrderStatus, now
from ..security import RateLimiter, client_ip
from . import pages
from .hosts import ShopScope, shop_of

if TYPE_CHECKING:
    from ..container import AppContext

UCP_VERSION = "2026-04-08"
SUPPORTED_VERSIONS = {UCP_VERSION}
SERVICE = "dev.ucp.shopping"
CHECKOUT = "dev.ucp.shopping.checkout"
FULFILLMENT = "dev.ucp.shopping.fulfillment"
SPEC = f"https://ucp.dev/{UCP_VERSION}"
MINOR_UNITS = {"IDR": 100}          # ISO 4217 exponent; anything not listed is treated as 2 decimals
SESSION_TTL = timedelta(minutes=30)
MAX_LINES = 10                      # line items per session
API_PREFIX = "/ucp/v1"
SESSION_KIND, IDEMPOTENCY_KIND = "ucp_session", "ucp_idempotency"

PAID_STATES = {OrderStatus.PAID, OrderStatus.ACCEPTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.PAID_OUT,
               OrderStatus.RETURN_REQUESTED, OrderStatus.RETURN_APPROVED, OrderStatus.RETURN_DISPUTED}
DEAD_STATES = {OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.REFUNDED}
AGENT_RE = re.compile(r'profile="([^"]*)"')
VERSION_RE = re.compile(r'version="([^"]*)"')


def to_minor(amount: int, currency: str = "IDR") -> int:
    return amount * MINOR_UNITS.get(currency, 100)


def rupiah(amount: int) -> str:
    return "Rp " + f"{amount:,}".replace(",", ".")


# ------------------------------------------------------------------ discovery
def ucp_profile(ctx: "AppContext", shop: ShopScope | None = None) -> dict:
    """The business profile. On a shop's own address the business is that shop: same capabilities, its own endpoint."""
    base = shop.origin if shop else ctx.settings.public_base_url.rstrip("/")
    return {"ucp": {
        "version": UCP_VERSION,
        "services": {SERVICE: [{
            "version": UCP_VERSION,
            "spec": f"{SPEC}/specification/overview",
            "transport": "rest",
            "schema": f"{SPEC}/services/shopping/rest.openapi.json",
            "endpoint": f"{base}{API_PREFIX}",
        }]},
        "capabilities": {
            CHECKOUT: [{"version": UCP_VERSION, "spec": f"{SPEC}/specification/checkout",
                        "schema": f"{SPEC}/schemas/shopping/checkout.json"}],
            FULFILLMENT: [{"version": UCP_VERSION, "spec": f"{SPEC}/specification/fulfillment",
                           "schema": f"{SPEC}/schemas/shopping/fulfillment.json", "extends": CHECKOUT}],
        },
        "payment_handlers": {},   # QRIS is finished by the buyer on continue_url, not through an instrument handler
    }}


# ------------------------------------------------------------------ errors and request shapes
class UcpError(Exception):
    def __init__(self, http: int, code: str, content: str, severity: str = "unrecoverable") -> None:
        self.http, self.code, self.content, self.severity = http, code, content, severity


def envelope(status: str = "success", with_capabilities: bool = True) -> dict:
    out: dict[str, Any] = {"version": UCP_VERSION, "status": status}
    if with_capabilities:
        out["capabilities"] = {CHECKOUT: [{"version": UCP_VERSION}], FULFILLMENT: [{"version": UCP_VERSION}]}
        out["payment_handlers"] = {}
    return out


def error_payload(code: str, content: str, severity: str = "unrecoverable", continue_url: str | None = None) -> dict:
    body: dict[str, Any] = {"ucp": envelope("error", with_capabilities=False),
                            "messages": [{"type": "error", "code": code, "content": content, "severity": severity}]}
    if continue_url:
        body["continue_url"] = continue_url
    return body


class _Refusal(Exception):
    """A cart the business will not take: a UCP error code and one plain sentence for the platform to show."""

    def __init__(self, code: str, content: str) -> None:
        self.code, self.content = code, content


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ItemIn(_Open):
    id: str = Field(min_length=1, max_length=64)


class LineItemIn(_Open):
    id: str | None = Field(default=None, max_length=64)
    item: ItemIn
    quantity: int = Field(ge=1, le=1_000_000)   # a huge quantity is a business outcome (out_of_stock), not a malformed request


class CheckoutIn(_Open):
    line_items: list[LineItemIn] | None = None
    buyer: dict | None = None
    fulfillment: dict | None = None


def parse_body(raw: bytes, require_items: bool) -> CheckoutIn:
    try:
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        body = CheckoutIn.model_validate(data)
    except ValidationError as e:
        detail = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise UcpError(422, "invalid_request", detail) from e
    except ValueError as e:
        raise UcpError(400, "invalid_request", f"request body is not valid JSON ({e})") from e
    if require_items and not body.line_items:
        raise UcpError(422, "invalid_request", "line_items: at least one line item is required")
    return body


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def option_id(rate: dict) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", f"{rate['courier']}-{rate['service']}".lower()).strip("-")


# ------------------------------------------------------------------ the service
class UcpCheckout:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx

    @property
    def base(self) -> str:
        return self.ctx.settings.public_base_url.rstrip("/")

    # ---- helpers
    def _origin(self, s: dict | None = None) -> str:
        """Links in a session point at the address it was started on (a shop's own address, or the platform host)."""
        return (s or {}).get("origin") or self.base

    def _url(self, s: dict) -> str:
        return f"{self._origin(s)}/checkout/{s['id']}"

    def _find_variant(self, item_id: str, seller_id: str | None = None):
        """The product and variant an item id names, if a buyer can buy it: published, and its seller is open. On a shop's own
        address only that shop's items exist."""
        for product in self.ctx.store.list_products(seller_id=seller_id, status="published"):
            for v in product.variants:
                if v.id == item_id:
                    seller = self.ctx.store.get_seller(product.seller_id)
                    return (product, v) if seller is not None and seller.open_for_buyers else (None, None)
        return None, None

    @staticmethod
    def _upgrade(s: dict) -> dict:
        """Sessions started before carts existed kept one product, variant and quantity at the top level: read them as one line."""
        s = dict(s)
        if "lines" not in s:
            s["lines"] = [{"product_id": s.pop("product_id"), "variant_id": s.pop("variant_id"), "quantity": s.pop("quantity")}]
        return s

    def _load(self, session_id: str, shop: ShopScope | None = None) -> dict:
        s = self.ctx.store.get_doc(SESSION_KIND, session_id)
        if s is None or (shop is not None and s.get("seller_id") != shop.seller_id):    # another shop's session does not exist here
            raise UcpError(404, "not_found", "checkout session not found")
        return self._upgrade(s)

    def _intake(self, line_items: list[LineItemIn], shop: ShopScope | None, refuse_sold_out: bool) -> tuple[list[dict], str]:
        """The session's lines for a request's line items, and the one seller they belong to. Raises _Refusal for a cart the
        business will not take: too many lines, an item that does not exist or is not this shop's, items of two shops, and
        (when refuse_sold_out) an item with nothing left. The same variant twice becomes one line."""
        if len(line_items) > MAX_LINES:
            raise _Refusal("unsupported", f"At most {MAX_LINES} line items per checkout session.")
        lines: dict[str, dict] = {}
        seller_id: str | None = None
        for li in line_items:
            product, variant = self._find_variant(li.item.id, shop.seller_id if shop else None)
            if product is None:
                raise _Refusal("item_unavailable", "That item does not exist or is not for sale.")
            if seller_id is None:
                seller_id = product.seller_id
            elif product.seller_id != seller_id:
                raise _Refusal("unsupported", "All line items must be from the same shop.")
            avail = variant.stock.available
            if refuse_sold_out and avail is not None and avail <= 0:
                what = "That item" if len(line_items) == 1 else f"{product.title} ({variant.label})"
                raise _Refusal("out_of_stock", f"{what} is out of stock.")
            line = lines.setdefault(variant.id, {"product_id": product.id, "variant_id": variant.id, "quantity": 0})
            line["quantity"] += li.quantity
        return list(lines.values()), seller_id

    def _resolve(self, s: dict) -> list[tuple[dict, Any, Any]]:
        """Each line with its product and variant, or (line, None, None) for one that can no longer be bought."""
        return [(line, *self._find_variant(line["variant_id"], s.get("seller_id"))) for line in s["lines"]]

    @staticmethod
    def _buyer_in(raw: dict | None) -> dict:
        raw = raw or {}
        return {k: _text(raw.get(k), 80 if k != "email" else 120) for k in ("first_name", "last_name", "email", "phone_number")}

    @staticmethod
    def _destination_in(fulfillment: dict | None) -> dict | None:
        """First shipping destination of the request, or None. Only domestic home delivery is supported."""
        for method in (fulfillment or {}).get("methods") or []:
            if not isinstance(method, dict) or method.get("type", "shipping") != "shipping":
                continue
            dests = [d for d in method.get("destinations") or [] if isinstance(d, dict)]
            wanted = method.get("selected_destination_id")
            dest = next((d for d in dests if wanted and d.get("id") == wanted), dests[0] if dests else None)
            if dest is None:
                return None
            given = _text(dest.get("id"), 64)
            return {"id": given if re.fullmatch(r"[A-Za-z0-9_-]+", given) else "dest_1",
                    "street_address": _text(dest.get("street_address"), 200),
                    "address_locality": _text(dest.get("address_locality"), 60),
                    "address_region": _text(dest.get("address_region"), 60),
                    "postal_code": _text(dest.get("postal_code"), 10),
                    "address_country": _text(dest.get("address_country"), 2).upper() or "ID"}
        return None

    @staticmethod
    def _selected_option_in(fulfillment: dict | None) -> str | None:
        for method in (fulfillment or {}).get("methods") or []:
            for group in (method.get("groups") or []) if isinstance(method, dict) else []:
                if isinstance(group, dict) and group.get("selected_option_id"):
                    return str(group["selected_option_id"])[:80]
        return None

    def _quote_cart(self, s: dict, postal_code: str) -> list[dict]:
        """Courier rates for the whole cart, cheapest first: summed weight and value, priced the way the order will be."""
        seller = self.ctx.store.get_seller(s["seller_id"])
        weight = value = 0
        for line, product, variant in self._resolve(s):
            if product is None or variant is None:
                raise KeyError(line["variant_id"])
            weight += max(1, variant.weight_gram) * line["quantity"]
            value += variant.price * line["quantity"]
        rates = self.ctx.checkout._quote(seller.origin.postal_code if seller and seller.origin else "", postal_code, weight, value)
        return [r.model_dump() for r in sorted(rates, key=lambda r: r.price)]

    def _refresh_options(self, s: dict) -> None:
        """Courier quotes depend on postal code and the cart; fetch them once per change, not on every GET."""
        dest = s.get("destination")
        s["options"] = []
        if not dest or not re.fullmatch(r"[0-9]{5}", dest.get("postal_code", "")) or dest.get("address_country") != "ID":
            return
        try:
            rates = self._quote_cart(s, dest["postal_code"])
        except (CheckoutError, KeyError):
            return
        s["options"] = [{"id": option_id(r), "courier": r["courier"], "service": r["service"], "price": r["price"],
                         "etd": r.get("etd", "")} for r in rates]
        if s.get("option_id") not in {o["id"] for o in s["options"]}:
            s["option_id"] = None

    def _apply(self, s: dict, body: CheckoutIn, replace: bool) -> None:
        """Copy what the request says into the session. Fields the request leaves out stay as they were."""
        if body.buyer is not None:
            s["buyer"] = self._buyer_in(body.buyer)
        changed_route = False
        if body.fulfillment is not None:
            dest = self._destination_in(body.fulfillment)
            if dest != s.get("destination"):
                s["destination"], changed_route = dest, True
            wanted = self._selected_option_in(body.fulfillment)
            s["option_id"] = wanted if wanted else (None if replace and changed_route else s.get("option_id"))
        if changed_route or "options" not in s:
            self._refresh_options(s)
        if s.get("option_id") and s["option_id"] not in {o["id"] for o in s["options"]}:
            s["option_id"] = None

    # ---- the view of a session
    def _problems(self, s: dict, resolved: list[tuple[dict, Any, Any]]) -> list[dict]:
        out: list[dict] = []

        def err(code: str, path: str, content: str, severity: str = "recoverable") -> None:
            out.append({"type": "error", "code": code, "path": path, "content": content, "severity": severity})

        cap = self.ctx.settings.max_qty_per_order
        units, over = 0, False
        for i, (line, product, variant) in enumerate(resolved):
            q = line["quantity"]
            path = f"$.line_items[{i}].quantity"
            units += q
            if units > cap and not over:       # the cap is for the whole cart: say it once, at the line that crosses it
                over = True
                err("quantity_exceeded", path, f"At most {cap} units per checkout.")
            avail = variant.stock.available
            if avail is not None and avail < q:
                which = f"{product.title} ({variant.label}): " if len(resolved) > 1 else ""
                err("out_of_stock", path, which + (f"Only {avail} left in stock." if avail else "This item is out of stock."),
                    "recoverable" if avail else "unrecoverable")
        buyer, dest = s["buyer"], s.get("destination")
        if not buyer.get("first_name"):
            err("missing", "$.buyer.first_name", "The buyer's name is required for delivery.")
        if not buyer.get("phone_number"):
            err("missing", "$.buyer.phone_number", "The buyer's phone number is required; the courier and the seller use it.")
        if not dest:
            err("missing", "$.fulfillment.methods[0].destinations", "A delivery address is required.")
            return out
        if dest["address_country"] != "ID":
            err("unsupported_destination", "$.fulfillment.methods[0].destinations[0].address_country",
                "Delivery is domestic (Indonesia) only.")
            return out
        probe = Address(name=" ".join(x for x in (buyer.get("first_name"), buyer.get("last_name")) if x) or "-",
                        phone=buyer.get("phone_number") or "0000000000", line1=dest["street_address"],
                        city=dest["address_locality"], postal_code=dest["postal_code"], country=dest["address_country"])
        from ..checkout.service import validate_buyer
        try:
            validate_buyer(probe)
        except CheckoutError as e:
            msg = str(e)
            path = ("$.buyer.phone_number" if "phone" in msg else
                    "$.fulfillment.methods[0].destinations[0].postal_code" if "postal" in msg else
                    "$.fulfillment.methods[0].destinations[0].street_address")
            if "phone" not in msg or buyer.get("phone_number"):
                err("invalid", path, msg[0].upper() + msg[1:] + ".")
        if not s["options"]:
            err("no_courier", "$.fulfillment.methods[0].destinations[0].postal_code",
                "No courier serves this address.", "unrecoverable")
        elif not s.get("option_id"):
            err("missing", "$.fulfillment.methods[0].groups[0].selected_option_id", "Choose a shipping option.")
        return out

    def view(self, s: dict) -> dict:
        ctx = self.ctx
        resolved = self._resolve(s)
        order = ctx.store.get_order(s["order_id"]) if s.get("order_id") else None
        currency = order.currency if order else "IDR"
        messages: list[dict] = []
        expires = datetime.fromisoformat(s["expires_at"])

        # state ---------------------------------------------------------------------------------
        if order is not None:
            expires = order.expires_at or expires
            if order.status in PAID_STATES or (order.status == OrderStatus.REFUNDED and order.shipment is not None):
                status = "completed"     # the sale happened; a later return or courier refund is not a cancelled checkout
            elif order.status in DEAD_STATES:
                status = "canceled"
            else:
                status = "complete_in_progress"
                if order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION:
                    messages.append({"type": "info", "code": "seller_confirms_stock", "content":
                                     "The seller is confirming stock. The payment code appears on continue_url once confirmed."})
                else:   # the buyer has to authorise the payment in a trusted UI: that is what requires_escalation is for
                    status = "requires_escalation"
                    messages.append({"type": "error", "code": "payment_required", "severity": "requires_buyer_review",
                                     "content": "Send the buyer to continue_url to scan the QRIS code and pay. "
                                                "Stock is held until expires_at."})
        elif s.get("canceled") or expires <= now():
            status = "canceled"
        elif any(product is None or variant is None for _, product, variant in resolved):
            status = "canceled"
            messages.append({"type": "error", "code": "item_unavailable", "severity": "unrecoverable",
                             "content": "This item is no longer available." if len(resolved) == 1
                             else "An item in this checkout is no longer available."})
        else:
            blocking = self._problems(s, resolved)
            messages.extend(blocking)
            status = "incomplete" if any(m["type"] == "error" for m in blocking) else "ready_for_complete"

        # content -------------------------------------------------------------------------------
        line_items: list[dict] = []
        cart_total = 0
        for n, (line, product, variant) in enumerate(resolved, 1):
            title = (f"{product.title} ({variant.label})" if product and variant else "Unavailable item")
            price = variant.price if variant else 0
            li_total = to_minor(price * line["quantity"], currency)
            cart_total += li_total
            item: dict[str, Any] = {"id": line["variant_id"], "title": title, "price": to_minor(price, currency)}
            if product and product.images:
                item["image_url"] = ctx.catalog.image_url(product.images[0])
            line_items.append({"id": f"li_{n}", "item": item, "quantity": line["quantity"],
                               "totals": [{"type": "subtotal", "amount": li_total}, {"type": "total", "amount": li_total}]})

        totals = [{"type": "subtotal", "amount": cart_total}]
        chosen = next((o for o in s["options"] if o["id"] == s.get("option_id")), None)
        shipping_amount = order.shipping_fee if order else (chosen["price"] if chosen else None)
        grand = cart_total
        product = next((p for _, p, _v in resolved if p is not None), None)
        if shipping_amount is not None:
            fee = to_minor(shipping_amount, currency)
            totals.append({"type": "fulfillment", "display_text": "Shipping", "amount": fee})
            grand += fee
        totals.append({"type": "total", "amount": grand})

        out: dict[str, Any] = {"ucp": envelope(), "id": s["id"], "status": status, "currency": currency,
                               "line_items": line_items, "totals": totals, "links": self._links(product, s),
                               "expires_at": expires.isoformat()}
        buyer = {k: v for k, v in s["buyer"].items() if v}
        if buyer:
            out["buyer"] = buyer
        out["payment"] = {"instruments": []}   # nothing to collect: the buyer pays by QRIS on continue_url
        out["fulfillment"] = self._fulfillment_view(s, order, currency)
        if messages:
            out["messages"] = messages
        if status == "completed":
            out["order"] = {"id": order.id, "label": order.id, "permalink_url": self._url(s)}
        elif status != "canceled":
            out["continue_url"] = self._url(s)
        return out

    def _links(self, product, s: dict) -> list[dict]:
        """The seller's return policy as a UCP `refund_policy` link (a well-known link type in the checkout spec). A cart is one
        shop's, so any of its products names the seller."""
        if product is None:
            return []
        return [{"type": "refund_policy", "title": "Return policy",
                 "url": f"{self._origin(s)}/stores/{product.seller_id}/returns"}]

    def _fulfillment_view(self, s: dict, order, currency: str) -> dict:
        ids = [f"li_{n}" for n in range(1, len(s["lines"]) + 1)]
        method: dict[str, Any] = {"id": "shipping_1", "type": "shipping", "line_item_ids": ids, "destinations": []}
        dest = s.get("destination")
        if dest:
            method["destinations"] = [{**{k: v for k, v in dest.items() if v}, "type": "shipping_address"}]
            method["selected_destination_id"] = dest["id"]
            if s["options"]:
                method["groups"] = [{
                    "id": "package_1", "line_item_ids": ids,
                    "selected_option_id": s.get("option_id"),
                    "options": [{"id": o["id"], "title": f"{o['courier'].upper()} {o['service']}".strip(),
                                 "description": {"plain": f"Estimated delivery: {o['etd']}" if o["etd"] else "Courier delivery"},
                                 "totals": [{"type": "total", "amount": to_minor(o["price"], currency)}]}
                                for o in s["options"]],
                }]
                if not s.get("option_id"):
                    del method["groups"][0]["selected_option_id"]
        return {"methods": [method]}

    # ---- operations (each returns (http_status, payload))
    def create(self, raw: bytes, agent: str = "", shop: ShopScope | None = None) -> tuple[int, dict]:
        home = (shop.origin if shop else self.base) + "/"
        body = parse_body(raw, require_items=True)
        try:
            lines, seller_id = self._intake(body.line_items, shop, refuse_sold_out=True)
        except _Refusal as r:
            return 200, error_payload(r.code, r.content, continue_url=home)
        s = {"id": "chk_" + secrets.token_hex(12), "lines": lines,
             "seller_id": seller_id, "origin": shop.origin if shop else self.base,
             "buyer": self._buyer_in(None), "destination": None, "option_id": None,
             "options": [], "order_id": None, "canceled": False, "completing": False, "agent": agent,
             "created_at": now().isoformat(), "expires_at": (now() + SESSION_TTL).isoformat()}
        self._apply(s, body, replace=False)
        self.ctx.store.put_doc(SESSION_KIND, s["id"], s)
        return 201, self.view(s)

    def get(self, session_id: str, shop: ShopScope | None = None) -> tuple[int, dict]:
        return 200, self.view(self._load(session_id, shop))

    def update(self, session_id: str, raw: bytes, shop: ShopScope | None = None) -> tuple[int, dict]:
        body = parse_body(raw, require_items=False)
        s = self._load(session_id, shop)
        if s.get("order_id") or s.get("completing") or s.get("canceled"):
            current = self.view(s)
            if current["status"] in ("completed", "canceled"):
                raise UcpError(409, "checkout_closed", f"This checkout is {current['status']} and cannot be changed.")
            current.setdefault("messages", []).append(     # still being paid: leave it unchanged (spec: recoverable error)
                {"type": "error", "code": "not_editable", "severity": "recoverable",
                 "content": "This checkout is already being completed, so it cannot be changed."})
            return 200, current
        if body.line_items:      # the request's line items replace the cart
            try:
                lines, seller_id = self._intake(body.line_items, shop, refuse_sold_out=False)
            except _Refusal as r:
                raise UcpError(422, r.code, r.content) from r
            if lines != s["lines"]:
                s.update(lines=lines, seller_id=seller_id, option_id=None, options=[])
                self._refresh_options(s)
        self._apply(s, body, replace=True)
        self.ctx.store.put_doc(SESSION_KIND, s["id"], s)
        return 200, self.view(s)

    def complete(self, session_id: str, raw: bytes, shop: ShopScope | None = None) -> tuple[int, dict]:
        parse_body(raw, require_items=False)
        s = self._load(session_id, shop)
        current = self.view(s)
        if current["status"] in ("completed", "canceled"):
            raise UcpError(409, "checkout_closed", f"This checkout is already {current['status']}.")
        if current["status"] != "ready_for_complete":
            if current["status"] == "incomplete":   # tell the platform what is still missing
                current.setdefault("messages", []).append(
                    {"type": "error", "code": "not_ready", "severity": "recoverable",
                     "content": "The checkout is missing information; resolve the other messages first."})
            return 200, current

        claimed: dict[str, bool] = {}

        def claim(d: dict) -> bool:
            claimed.clear()        # Firestore may run the callback more than once
            if d.get("order_id") or d.get("completing") or d.get("canceled"):
                return False
            d["completing"] = True
            claimed["yes"] = True
            return True
        self.ctx.store.update_doc(SESSION_KIND, session_id, claim)
        if not claimed:            # a concurrent complete won; both callers see the same checkout
            return 200, self.view(self._load(session_id))

        dest, buyer = s["destination"], s["buyer"]
        option = next(o for o in s["options"] if o["id"] == s["option_id"])
        address = Address(name=" ".join(x for x in (buyer.get("first_name"), buyer.get("last_name")) if x),
                          phone=buyer["phone_number"], line1=dest["street_address"], city=dest["address_locality"],
                          postal_code=dest["postal_code"], country=dest["address_country"])
        try:
            cart = [{"product_id": line["product_id"], "variant_id": line["variant_id"], "qty": line["quantity"]}
                    for line in s["lines"]]
            order = self.ctx.checkout.create_checkout(buyer=address, courier=option["courier"], service=option["service"],
                                                      channel="ucp", referrer=s.get("agent", ""), items=cart)
        except (CheckoutError, KeyError) as e:
            self.ctx.store.update_doc(SESSION_KIND, session_id, lambda d: d.update(completing=False) or True)
            text = str(e)
            code = "out_of_stock" if "stock" in text else "checkout_failed"
            failed = self.view(self._load(session_id))
            failed.setdefault("messages", []).append({"type": "error", "code": code, "severity": "recoverable",
                                                      "content": text[:200]})
            return 200, failed
        self.ctx.store.update_doc(SESSION_KIND, session_id, lambda d: d.update(order_id=order.id) or True)
        return 200, self.view(self._load(session_id))

    def cancel(self, session_id: str, shop: ShopScope | None = None) -> tuple[int, dict]:
        s = self._load(session_id, shop)
        if s.get("order_id"):
            order = self.ctx.checkout.cancel_unpaid(s["order_id"])
            if order is not None and order.status in PAID_STATES:
                raise UcpError(409, "cannot_cancel", "This order is already paid. Ask the seller to cancel it.")
        else:
            self.ctx.store.update_doc(SESSION_KIND, session_id, lambda d: d.update(canceled=True) or True)
        return 200, self.view(self._load(session_id))

    # ---- hosted payment page (the continue_url)
    def pay_page(self, session_id: str) -> str:
        s = self.ctx.store.get_doc(SESSION_KIND, session_id)
        if s is None:
            return pages.layout("Checkout not found", pages.status("info", "This checkout link is not valid",
                                                                   "<p>Ask the shopping assistant to start the checkout again.</p>", icon="search"),
                                heading=False)
        s = self._upgrade(s)
        v = self.view(s)
        order = self.ctx.store.get_order(s["order_id"]) if s.get("order_id") else None
        amount = {t["type"]: t["amount"] // 100 for t in v["totals"]}
        lines = [(f"{li['item']['title']} × {li['quantity']}",
                  rupiah(next(t["amount"] for t in li["totals"] if t["type"] == "total") // 100)) for li in v["line_items"]]
        if "fulfillment" in amount:
            lines.append(("Shipping", rupiah(amount["fulfillment"])))
        rows = pages.summary(lines, total=("Total", rupiah(amount["total"])))
        total = amount["total"]
        live = ""
        if order is None:
            body = (pages.status("info", "Not placed yet", "<p>This checkout is not placed yet. The shopping assistant will finish it for you.</p>")
                    + rows)
        elif order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION:
            live = order.status.value
            body = (pages.status("wait", "Waiting for the seller", "<p>The seller is confirming stock. This page updates by itself.</p>") + rows)
        elif order.status == OrderStatus.AWAITING_PAYMENT and order.payment:
            live = f"{order.status.value}:{order.payment.status}"
            left = max(0, int((order.expires_at - now()).total_seconds())) if order.expires_at else None
            body = (pages.pay_card(rupiah(total), order.payment.qr_string, left, pages.clock(order.expires_at) if order.expires_at else "") + rows)
        elif order.status in PAID_STATES:
            body = (pages.status("ok", "Paid", "<p>The seller will ship your order.</p>") + rows
                    + f'<p class=links><a href="{escape(self._origin(s))}/stores/{escape(order.seller_id)}/returns">{pages.ico("shield", "sm")}Return policy</a></p>')
        elif order.status == OrderStatus.REFUNDED:
            body = (pages.status("ok", "Refunded", "<p>This order was refunded. If the money has not reached you yet, ask the shopping assistant for the status.</p>")
                    + rows)
        else:
            body = pages.status("bad", "Expired or cancelled", "<p>This checkout expired or was cancelled. Nothing was charged.</p>") + rows
        return pages.layout("Pay for your order", f"<div class=stack><h1 class=sr>Pay for your order</h1>{body}</div>", live=live, heading=False)


PAY_PAGE_CSP = pages.csp()


# ------------------------------------------------------------------ HTTP layer
def build_router(ctx: "AppContext", limits: RateLimiter) -> APIRouter:
    svc = UcpCheckout(ctx)
    router = APIRouter(prefix=API_PREFIX, tags=["ucp"])

    def respond(status: int, payload: dict) -> JSONResponse:
        return JSONResponse(payload, status_code=status)

    def check_headers(request: Request) -> None:
        agent = request.headers.get("ucp-agent", "")
        if not AGENT_RE.search(agent):
            raise UcpError(400, "invalid_request", 'The UCP-Agent header with profile="<url>" is required.')
        m = VERSION_RE.search(agent)
        if m and m.group(1) not in SUPPORTED_VERSIONS:
            raise UcpError(422, "version_unsupported",
                           f"UCP version {m.group(1)[:20]} is not supported; supported: {', '.join(sorted(SUPPORTED_VERSIONS))}.")

    def throttle(request: Request, bucket: str, limit: int, window: int) -> None:
        if not limits.allow(f"ucp-{bucket}:{client_ip(request)}", limit, window):
            raise UcpError(429, "rate_limited", "Too many requests, slow down.")

    async def run(request: Request, bucket: tuple[str, int, int], fn: Callable[[bytes], tuple[int, dict]]) -> JSONResponse:
        """Common path: headers, rate limit, idempotency replay, and UcpError -> error envelope."""
        try:
            check_headers(request)
            throttle(request, *bucket)
            raw = await request.body()
            key = request.headers.get("idempotency-key", "")[:128]
            if not key:
                return respond(*await _in_thread(fn, raw))
            shop = shop_of(request)
            scope = f"{request.method} {request.url.path} {shop.seller_id if shop else ''}"    # one key on two shops is two requests
            doc_id = hashlib.sha256(f"{scope}|{key}".encode()).hexdigest()
            fingerprint = hashlib.sha256(raw).hexdigest()
            seen = ctx.store.get_doc(IDEMPOTENCY_KIND, doc_id)
            if seen:
                if seen["fingerprint"] != fingerprint:
                    raise UcpError(409, "idempotency_conflict", "This Idempotency-Key was already used with a different request.")
                return respond(seen["status"], seen["payload"])
            status, payload = await _in_thread(fn, raw)
            ctx.store.put_doc(IDEMPOTENCY_KIND, doc_id, {"fingerprint": fingerprint, "status": status, "payload": payload})
            return respond(status, payload)
        except UcpError as e:
            return respond(e.http, error_payload(e.code, e.content, e.severity))

    @router.post("/checkout-sessions")
    async def create(request: Request):
        agent = attribution.agent_from_ucp_header(request.headers.get("ucp-agent"))   # the platform's host, kept on the order
        shop = shop_of(request)
        return await run(request, ("create", 120, 60), lambda raw: svc.create(raw, agent, shop))

    @router.get("/checkout-sessions/{session_id}")
    async def read(request: Request, session_id: str):
        shop = shop_of(request)
        return await run(request, ("read", 600, 60), lambda raw: svc.get(session_id, shop))

    @router.put("/checkout-sessions/{session_id}")
    async def update(request: Request, session_id: str):
        shop = shop_of(request)
        return await run(request, ("update", 240, 60), lambda raw: svc.update(session_id, raw, shop))

    @router.post("/checkout-sessions/{session_id}/complete")
    async def complete(request: Request, session_id: str):
        shop = shop_of(request)
        return await run(request, ("complete", 30, 3600), lambda raw: svc.complete(session_id, raw, shop))

    @router.post("/checkout-sessions/{session_id}/cancel")
    async def cancel(request: Request, session_id: str):
        shop = shop_of(request)
        return await run(request, ("cancel", 120, 60), lambda raw: svc.cancel(session_id, shop))

    return router


async def _in_thread(fn: Callable[[bytes], tuple[int, dict]], raw: bytes) -> tuple[int, dict]:
    import asyncio
    return await asyncio.to_thread(fn, raw)


def policy_page_response(ctx: "AppContext", seller_id: str) -> HTMLResponse:
    """The seller's return policy as a plain page: the target of the UCP refund_policy link."""
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None or not seller.open_for_buyers:
        return HTMLResponse(pages.layout("Return policy", pages.status("info", "This store was not found", icon="search"), heading=False),
                            status_code=404, headers={"Content-Security-Policy": PAY_PAGE_CSP})
    policy = ctx.returns.policy_view(seller)
    back = (f"/stores/{seller.id}", seller.name)
    if not policy["window_hours"]:
        body = pages.status("info", "This store does not offer returns through Etalase", "<p>Check the item details before you order.</p>", icon="alert")
    else:
        hours = policy["window_hours"]
        reasons = "".join(f"<li>{pages.ico('check')}<span>{escape(_REASON_TEXT.get(r, r.replace('_', ' ')))}</span></li>" for r in policy["reasons"])
        refund = "".join(f"<li>{pages.ico('wallet')}<span><b>{escape(k.replace('_', ' ').capitalize())}:</b> {escape(v)}</span></li>" for k, v in policy["refund"].items())
        body = (pages.status("ok", f"Returns accepted for {pages.window(hours)} after delivery",
                             f"<p>That is {hours} hours, counted from the moment the courier confirms delivery.</p>", icon="shield")
                + f'<section class=band><h2 class=cap>You can return an item if it is</h2><ul class=checks>{reasons}</ul></section>'
                + f'<section class=band><h2 class=cap>What you get back</h2><ul class=checks>{refund}</ul></section>'
                + '<section class=band><h2 class=cap>How it works</h2><ol class=num>'
                  "<li>Ask for the return from your order page, or through your shopping assistant.</li>"
                  f"<li>The seller answers within {policy['seller_answers_within_hours']} hours. No answer counts as approval.</li>"
                  "<li>You send the item back and pay that shipping. When the seller confirms it arrived, you are refunded.</li></ol>"
                  '<p class=fine style="margin-top:.75rem"><a href="/policy/payouts">How payouts are held, and who decides a dispute</a></p></section>')
    if policy["note"]:
        body += f"<div class=sellernote><b>A note from the seller (written by the seller, not by Etalase)</b>{escape(policy['note'])}</div>"
    return HTMLResponse(pages.layout("Return policy", f"<div class=stack>{body}</div>", h1=f"Return policy: {seller.name}", back=back),
                        headers={"Content-Security-Policy": PAY_PAGE_CSP})


_REASON_TEXT = {"damaged": "Damaged", "wrong_item": "Not the item you ordered", "not_as_described": "Not as described",
                "changed_mind": "Returned because you changed your mind"}


def pay_page_response(ctx: "AppContext", session_id: str) -> HTMLResponse:
    html = UcpCheckout(ctx).pay_page(session_id[:64])
    return HTMLResponse(html, headers={"Content-Security-Policy": PAY_PAGE_CSP, "Cache-Control": "no-store"})
