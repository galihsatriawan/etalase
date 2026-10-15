"""Agent-facing operations. Exposed over MCP (gateway/mcp_server.py) and REST.

Results are plain dicts with a `next_step` hint, and errors come back as {"error": ...}
instead of exceptions, which is what LLM agents handle best.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Address, CheckoutError, Order, OrderStatus

if TYPE_CHECKING:
    from ..container import AppContext

NEXT_STEP = {
    OrderStatus.AWAITING_SELLER_CONFIRMATION: "The seller is confirming stock (usually within minutes). Poll get_checkout_status; "
                                              "a QRIS payment code appears once confirmed.",
    OrderStatus.AWAITING_PAYMENT: "Show the QRIS code to the buyer. Stock is held until expires_at.",
    OrderStatus.PAID: "Paid. Waiting for the seller to accept and ship.",
    OrderStatus.ACCEPTED: "Paid and accepted. The seller is preparing the shipment.",
    OrderStatus.SHIPPED: "Shipped. Share the tracking number with the buyer.",
    OrderStatus.DELIVERED: "Delivered.",
    OrderStatus.PAID_OUT: "Delivered and completed.",
    OrderStatus.REJECTED: "The seller is out of stock. Nothing was charged. Offer the buyer alternatives.",
    OrderStatus.EXPIRED: "The checkout expired unpaid. Create a new checkout if the buyer still wants it.",
    OrderStatus.REFUNDED: "The seller could not fulfil the order. The payment was refunded.",
}


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
        "next_step": NEXT_STEP.get(order.status, ""),
    }


class GatewayTools:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx

    def search_products(self, query: str, max_price_idr: int | None = None, ship_to_country: str = "ID",
                        limit: int = 5) -> dict:
        results = self.ctx.catalog.search(query, max_price=max_price_idr, ship_to_country=ship_to_country,
                                          limit=min(max(limit, 1), 10))
        return {"results": results, "count": len(results),
                "note": "Prices in IDR. Availability 'confirm_with_seller' means the seller confirms stock before payment."}

    def get_product(self, product_id: str) -> dict:
        p = self.ctx.store.get_product(product_id)
        if p is None or p.status != "published":
            return {"error": "product not found"}
        return self.ctx.catalog.agent_view(p, self.ctx.store.get_seller(p.seller_id))

    def get_shipping_options(self, product_id: str, variant_id: str, quantity: int, postal_code: str) -> dict:
        try:
            return {"options": self.ctx.checkout.shipping_options(product_id, variant_id, quantity, postal_code),
                    "currency": "IDR"}
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}

    def create_checkout(self, product_id: str, variant_id: str, quantity: int, buyer_name: str, buyer_phone: str,
                        address_line: str, city: str, postal_code: str, country: str = "ID",
                        courier: str | None = None) -> dict:
        buyer = Address(name=buyer_name, phone=buyer_phone, line1=address_line, city=city,
                        postal_code=postal_code, country=country.upper())
        try:
            order = self.ctx.checkout.create_checkout(product_id, variant_id, quantity, buyer, courier)
        except (CheckoutError, KeyError) as e:
            return {"error": str(e)}
        return order_view(order)

    def get_checkout_status(self, checkout_id: str) -> dict:
        order = self.ctx.store.get_order(checkout_id)
        return order_view(order) if order else {"error": "checkout not found"}
