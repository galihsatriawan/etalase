"""Agent-facing operations. Exposed over MCP (gateway/mcp_server.py) and REST.

Results are plain dicts with a `next_step` hint, and errors come back as {"error": ...}
instead of exceptions, which is what LLM agents handle best.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Address, CheckoutError, Order, OrderStatus, SellMode, now

if TYPE_CHECKING:
    from ..container import AppContext
    from .hosts import ShopScope

NEXT_STEP = {
    OrderStatus.AWAITING_SELLER_CONFIRMATION: "The seller is confirming stock. Poll get_checkout_status; a QRIS payment code appears once "
                                              "they confirm. Nothing is charged before that.",
    OrderStatus.AWAITING_PAYMENT: "Show the QRIS code to the buyer. Stock is held until expires_at.",
    OrderStatus.PAID: "Paid. Waiting for the seller to accept and ship.",
    OrderStatus.ACCEPTED: "Paid and accepted. The seller is preparing the shipment.",
    OrderStatus.SHIPPED: "Shipped. Share the tracking number with the buyer.",
    OrderStatus.DELIVERED: "Delivered.",
    OrderStatus.RETURN_DISPUTED: "The seller disputes the return and an Etalase operator will review it. Nothing more to do now.",
    OrderStatus.PAID_OUT: "Delivered and completed. The return window has closed.",
    OrderStatus.REJECTED: "The seller is out of stock. Nothing was charged. Offer the buyer alternatives.",
    OrderStatus.EXPIRED: "The checkout expired unpaid. Create a new checkout if the buyer still wants it.",
    OrderStatus.REFUNDED: "The order ended and the buyer is being refunded: payment.status says whether the refund is done.",
}


def _item_product_id(item) -> str | None:
    """The product a cart item names, whether it is a dict or a (product_id, variant_id[, qty]) tuple."""
    if isinstance(item, dict):
        return item.get("product_id")
    if isinstance(item, (tuple, list)) and item:
        return item[0]
    return None


def _cart_items(items):
    """Cart lines as an agent sends them ({"product_id", "variant_id", "quantity"}) in the form the service takes ("qty").
    Anything else (a tuple, a line that already says "qty") passes through for the service to check."""
    if items is None:
        return None
    return [{"product_id": i.get("product_id"), "variant_id": i.get("variant_id"), "qty": i.get("qty", i.get("quantity", 1))}
            if isinstance(i, dict) else i for i in items]


def _wanted(product_id, cart) -> list:
    """Every product id a call names: the one product, or the product of each cart line."""
    return [_item_product_id(i) for i in cart] if cart is not None else [product_id]


def _unanswered(order: Order) -> bool:
    """A stock check the seller never answered: confirm-first, and no payment code was ever issued."""
    return order.sell_mode == SellMode.CONFIRM_FIRST and order.payment is None and not order.seller_confirmed


def next_step(order: Order) -> str:
    ship = order.shipment.status if order.shipment else ""
    if order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION and order.expires_at:
        return (f"The seller was asked whether the item is still available and has until {order.expires_at.isoformat(timespec='minutes')} "
                "to answer. Poll get_checkout_status; a QRIS payment code appears once they confirm. Nothing is charged before that. "
                "Tell the buyer how long this can take, and do not promise a quick answer.")
    if order.status == OrderStatus.EXPIRED and _unanswered(order):
        return ("The seller did not confirm stock in time, so the checkout expired. No payment code was ever issued and nothing was "
                "charged. Offer the buyer another seller, or create a new checkout later if the buyer still wants this one.")
    if order.status == OrderStatus.SHIPPED and ship == "returning":
        return ("The courier could not deliver the parcel and is bringing it back to the seller. The buyer is refunded in full "
                "when it arrives. Do not tell the buyer it is on its way.")
    if order.status == OrderStatus.SHIPPED and ship == "failed":
        return ("The courier reported a problem with this shipment and an Etalase operator has been alerted. Tell the buyer it "
                "is delayed, not that it is on its way. Poll get_checkout_status.")
    if order.status == OrderStatus.REFUNDED and not order.return_request and ship in ("lost", "returned"):
        why = "The courier lost the parcel" if ship == "lost" else "The courier brought the parcel back to the seller"
        done = order.payment is not None and order.payment.status == "refunded"
        return (f"{why}, so the buyer is refunded in full. " +
                ("The refund is complete." if done else "Our team sends the refund by hand, which can take a few days "
                                                       "(payment.status is refund_pending)."))
    if order.status == OrderStatus.DELIVERED and order.payout is None and order.payout_due_at and order.payout_due_at > now():
        return ("Delivered. If the item is damaged, wrong or not as described, call request_return before "
                f"{order.payout_due_at.isoformat(timespec='minutes')}.")
    r = order.return_request
    if order.status == OrderStatus.RETURN_REQUESTED and r:
        return (f"The return request is with the seller, who answers before {r.respond_by.isoformat(timespec='minutes')}; "
                "silence counts as approval. Poll get_checkout_status.")
    if order.status == OrderStatus.RETURN_APPROVED and r and r.lapse_at:
        return (f"The seller approved the return. Send the item back by {r.lapse_at.isoformat(timespec='minutes')}, then share "
                "the courier tracking number with submit_return_tracking. The refund follows when the seller confirms it arrived.")
    if order.status == OrderStatus.REFUNDED and order.return_request and order.return_request.outcome == "refunded":
        amount = f"Rp{order.return_request.refund_amount:,}".replace(",", ".")
        if order.payment and order.payment.status == "refunded":
            return f"The return is complete. The buyer is refunded {amount}."
        return (f"The return is complete. {amount} is owed to the buyer; our team sends the refund by hand, which can take "
                "a few days (payment.status is refund_pending).")
    return NEXT_STEP.get(order.status, "")


def return_view(order: Order) -> dict | None:
    r = order.return_request
    if r is not None:
        view = {"reason": r.reason.value, "outcome": r.outcome or None, "refund_amount": r.refund_amount or None}
        if order.status == OrderStatus.RETURN_REQUESTED:
            view["seller_must_answer_by"] = r.respond_by.isoformat(timespec="minutes")
        if order.status == OrderStatus.RETURN_APPROVED and r.lapse_at:
            view["item_must_reach_seller_by"] = r.lapse_at.isoformat(timespec="minutes")
        return view
    if order.status == OrderStatus.DELIVERED and order.payout is None and order.payout_due_at:
        return {"can_request_until": order.payout_due_at.isoformat(timespec="minutes")}
    return None


def order_view(order: Order) -> dict:
    return {
        "checkout_id": order.id,
        "status": order.status.value,
        "sell_mode": order.sell_mode.value,
        "currency": order.currency,
        "items": [i.model_dump() for i in order.items],
        "subtotal": order.subtotal,
        "shipping": order.shipping_rate.model_dump() if order.shipping_rate else None,
        "total": order.total,
        "payment": ({"method": order.payment.method, "qr_string": order.payment.qr_string, "status": order.payment.status}
                    if order.payment else None),
        "tracking": order.shipment.waybill if order.shipment else None,
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "delivered_at": order.delivered_at.isoformat(timespec="seconds") if order.delivered_at else None,
        "return": return_view(order),
        "next_step": next_step(order),
    }


class GatewayTools:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx

    def trust(self, seller_id: str) -> dict:
        """What an agent needs to weigh a seller: the return terms and how many orders the seller completed."""
        seller = self.ctx.store.get_seller(seller_id)
        return {"return_policy": self.ctx.returns.policy_view(seller), "track_record": self.ctx.checkout.track_record(seller_id)}

    def _visible(self, product_id: str | None, shop: "ShopScope | None"):
        """The product, if this caller may see it: published, its seller open to buyers, and, on a shop's own address
        (rina.<domain>), one of that shop's products. Anything else is "not found", never "not yours"."""
        p = self.ctx.store.get_product(product_id) if product_id else None
        if p is None or p.status != "published" or (shop is not None and p.seller_id != shop.seller_id):
            return None
        seller = self.ctx.store.get_seller(p.seller_id)
        return p if seller is not None and seller.open_for_buyers else None

    def _mine(self, checkout_id: str, shop: "ShopScope | None") -> bool:
        """False for a checkout that belongs to another shop when the call came in on a shop's own address."""
        if shop is None:
            return True
        order = self.ctx.store.get_order(checkout_id)
        return order is not None and order.seller_id == shop.seller_id

    def search_products(self, query: str, max_price_idr: int | None = None, ship_to_country: str = "ID",
                        limit: int = 5, shop: "ShopScope | None" = None) -> dict:
        results = self.ctx.catalog.search(query, max_price=max_price_idr, ship_to_country=ship_to_country,
                                          limit=min(max(limit, 1), 10), seller_id=shop.seller_id if shop else None)
        seen: dict[str, dict] = {}
        for r in results:
            sid = r["seller"]["id"]
            r["seller"].update(seen.setdefault(sid, self.trust(sid)))
        return {"results": results, "count": len(results),
                "note": "Prices in IDR. Availability 'confirm_with_seller' means the seller confirms stock before payment."}

    def get_product(self, product_id: str, shop: "ShopScope | None" = None) -> dict:
        p = self._visible(product_id, shop)
        if p is None:
            return {"error": "product not found"}
        view = self.ctx.catalog.agent_view(p, self.ctx.store.get_seller(p.seller_id))
        view["seller"].update(self.trust(p.seller_id))
        return view

    def get_shipping_options(self, product_id: str | None = None, variant_id: str | None = None, quantity: int = 1,
                             postal_code: str = "", shop: "ShopScope | None" = None, items: list[dict] | None = None) -> dict:
        """Courier options to a postal code, for one product or for a whole cart (`items`, all from one shop)."""
        cart = _cart_items(items)
        if any(self._visible(p, shop) is None for p in _wanted(product_id, cart)):
            return {"error": "product not found"}
        try:
            return {"options": self.ctx.checkout.shipping_options(product_id, variant_id, quantity, postal_code, items=cart),
                    "currency": "IDR"}
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}

    def create_checkout(self, product_id: str | None = None, variant_id: str | None = None, quantity: int = 1,
                        buyer_name: str = "", buyer_phone: str = "", address_line: str = "", city: str = "",
                        postal_code: str = "", country: str = "ID", courier: str | None = None,
                        channel: str = "", referrer: str = "", payment_method: str = "qris",
                        items: list[dict] | None = None, shop: "ShopScope | None" = None) -> dict:
        """`channel` and `referrer` are set by the door the call came through (REST, MCP), never taken from the agent's input.
        Every product asked for, one or several (`items`), must be visible to the caller: its seller open to buyers and, on a
        shop's own address, that shop's own."""
        cart = _cart_items(items)
        if any(self._visible(p, shop) is None for p in _wanted(product_id, cart)):
            return {"error": "product not found"}
        buyer = Address(name=buyer_name, phone=buyer_phone, line1=address_line, city=city,
                        postal_code=postal_code, country=country.upper())
        try:
            order = self.ctx.checkout.create_checkout(product_id, variant_id, quantity, buyer, courier,
                                                      channel=channel, referrer=referrer, payment_method=payment_method,
                                                      items=cart)
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}
        return order_view(order)

    def get_checkout_status(self, checkout_id: str, shop: "ShopScope | None" = None) -> dict:
        order = self.ctx.store.get_order(checkout_id)
        return order_view(order) if order and self._mine(checkout_id, shop) else {"error": "checkout not found"}

    def request_return(self, checkout_id: str, buyer_phone: str, reason: str, note: str = "",
                       evidence_urls: list[str] | None = None, shop: "ShopScope | None" = None) -> dict:
        if not self._mine(checkout_id, shop):
            return {"error": "checkout not found"}
        try:
            return order_view(self.ctx.returns.request(checkout_id, buyer_phone, reason, note, evidence_urls))
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}

    def submit_return_tracking(self, checkout_id: str, buyer_phone: str, waybill: str, shop: "ShopScope | None" = None) -> dict:
        if not self._mine(checkout_id, shop):
            return {"error": "checkout not found"}
        try:
            return order_view(self.ctx.returns.add_return_tracking(checkout_id, buyer_phone, waybill))
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}
