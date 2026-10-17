"""MCP server: the tools a buyer's AI agent calls (search, shipping, checkout, status).

Served over streamable HTTP at /mcp/ by the FastAPI app. Any MCP client works, including
ADK's McpToolset (see agents/buyer_agent.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .. import attribution
from ..security import RateLimiter, client_ip
from .hosts import ShopScope, shop_of

if TYPE_CHECKING:
    from ..container import AppContext

INSTRUCTIONS = """Etalase lets you buy from independent Indonesian sellers who sell on WhatsApp.
Flow: search_products -> get_shipping_options -> create_checkout -> show the QRIS code -> get_checkout_status.
A buyer can take several products from the same shop in one order: pass items to get_shipping_options and create_checkout.
Only domestic delivery inside Indonesia is supported. Prices are in IDR. Payment is by QRIS only, before the seller packs the
order: cash on delivery is not offered, so never promise it to a buyer.
On the address of a single shop (rina.<domain>/mcp) every tool only sees that shop's products and orders.
If a variant's availability is 'confirm_with_seller', tell the buyer the seller confirms stock first.
Each seller shows its return_policy and track_record: weigh them for the buyer. After delivery, if the item is damaged,
wrong or not as described, call request_return (get_checkout_status says until when); then submit_return_tracking."""


# (bucket, calls, window in seconds) per client address. The buckets are shared with the REST routes of the same name in
# api/main.py, so an agent cannot get around a limit by switching from REST to MCP. A shipping quote costs a courier-API
# call and a checkout holds stock for 15 minutes, which is why those two are the tightest.
TOOL_LIMITS = {
    "search_products": ("search", 120, 60),
    "get_product": ("search", 120, 60),
    "get_shipping_options": ("shipping", 60, 60),
    "create_checkout": ("checkout", 20, 3600),
    "get_checkout_status": ("status", 240, 60),
    "request_return": ("return", 10, 3600),
    "submit_return_tracking": ("return", 10, 3600),
}


def build_mcp(ctx: "AppContext", limits: RateLimiter | None = None) -> FastMCP:
    mcp = FastMCP(
        "etalase",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # Cloud Run serves on its own hostname; restrict origins at the load balancer or with auth instead.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    tools = ctx.gateway
    limits = limits or RateLimiter()

    def too_many(tool: str, mcp_ctx: Context) -> dict | None:
        """None when the call may go ahead. Over the limit the agent gets an error it can read, like any other tool error.
        Calls without an HTTP request behind them (in-process tests, stdio) are not limited."""
        try:
            request = mcp_ctx.request_context.request if mcp_ctx is not None else None
        except ValueError:          # FastMCP raises this when the tool is called outside a request
            request = None
        if request is None:
            return None
        bucket, limit, window = TOOL_LIMITS[tool]
        if limits.allow(f"{bucket}:{client_ip(request)}", limit, window):
            return None
        return {"error": "rate_limited", "message": "Too many requests from this client. Wait a minute and try again."}

    def shop_for(mcp_ctx: Context) -> ShopScope | None:
        """The shop whose own address (rina.<domain>/mcp) this call came in on, or None on the platform host and in-process.
        On a shop's address every tool only sees that shop."""
        try:
            request = mcp_ctx.request_context.request if mcp_ctx is not None else None
        except ValueError:
            request = None
        return shop_of(request) if request is not None else None

    def caller_of(mcp_ctx: Context) -> str:
        """The calling client's product name from its User-Agent, as a label for the order ("" when there is no HTTP request)."""
        try:
            request = mcp_ctx.request_context.request if mcp_ctx is not None else None
        except ValueError:
            request = None
        return attribution.agent_from_user_agent(request.headers.get("user-agent")) if request is not None else ""

    @mcp.tool()
    def search_products(query: str, max_price_idr: int | None = None, ship_to_country: str = "ID",
                        limit: int = 5, mcp_ctx: Context = None) -> dict:
        """Search products from WhatsApp sellers. Query in English or Bahasa Indonesia, e.g. 'black voal hijab'."""
        if blocked := too_many("search_products", mcp_ctx):
            return blocked
        return tools.search_products(query, max_price_idr, ship_to_country, limit, shop=shop_for(mcp_ctx))

    @mcp.tool()
    def get_product(product_id: str, mcp_ctx: Context = None) -> dict:
        """Get one product with its variants, prices and availability."""
        if blocked := too_many("get_product", mcp_ctx):
            return blocked
        return tools.get_product(product_id, shop=shop_for(mcp_ctx))

    @mcp.tool()
    def get_shipping_options(postal_code: str, product_id: str | None = None, variant_id: str | None = None,
                             quantity: int = 1, items: list[dict] | None = None, mcp_ctx: Context = None) -> dict:
        """Courier options and prices to the buyer's postal code. For one product give product_id, variant_id and quantity.
        For several products from the same shop give items, each {"product_id", "variant_id", "quantity"}: the quote covers
        the whole cart."""
        if blocked := too_many("get_shipping_options", mcp_ctx):
            return blocked
        return tools.get_shipping_options(product_id, variant_id, quantity, postal_code, shop=shop_for(mcp_ctx), items=items)

    @mcp.tool()
    def create_checkout(buyer_name: str, buyer_phone: str, address_line: str, city: str, postal_code: str,
                        product_id: str | None = None, variant_id: str | None = None, quantity: int = 1,
                        country: str = "ID", courier: str | None = None, payment_method: str = "qris",
                        items: list[dict] | None = None, mcp_ctx: Context = None) -> dict:
        """Reserve stock and start checkout. Returns a QRIS payment code, or a pending state while the seller confirms stock.
        For one product give product_id, variant_id and quantity. For several products from the same shop (one order, one
        payment, one shipment) give items, each {"product_id", "variant_id", "quantity"}, at most 10 lines; either all of them
        are reserved or none.
        Only call this after the buyer explicitly agreed to buy. payment_method must be "qris": cash on delivery is not offered."""
        if blocked := too_many("create_checkout", mcp_ctx):
            return blocked
        return tools.create_checkout(product_id, variant_id, quantity, buyer_name, buyer_phone, address_line,
                                     city, postal_code, country, courier, channel="mcp", referrer=caller_of(mcp_ctx),
                                     payment_method=payment_method, items=items, shop=shop_for(mcp_ctx))

    @mcp.tool()
    def get_checkout_status(checkout_id: str, mcp_ctx: Context = None) -> dict:
        """Current status of a checkout, the payment code once available, and tracking after shipment."""
        if blocked := too_many("get_checkout_status", mcp_ctx):
            return blocked
        return tools.get_checkout_status(checkout_id, shop=shop_for(mcp_ctx))

    @mcp.tool()
    def request_return(checkout_id: str, buyer_phone: str, reason: str, note: str = "",
                       evidence_urls: list[str] | None = None, mcp_ctx: Context = None) -> dict:
        """Ask for a return after delivery, inside the return window (see get_checkout_status). reason is one of damaged,
        wrong_item, not_as_described, changed_mind (changed_mind only if the seller's return_policy lists it).
        buyer_phone must be the phone used at checkout. evidence_urls: up to 5 https links to photos."""
        if blocked := too_many("request_return", mcp_ctx):
            return blocked
        return tools.request_return(checkout_id, buyer_phone, reason, note, evidence_urls, shop=shop_for(mcp_ctx))

    @mcp.tool()
    def submit_return_tracking(checkout_id: str, buyer_phone: str, waybill: str, mcp_ctx: Context = None) -> dict:
        """After the seller approved a return: the courier tracking number of the parcel going back to the seller."""
        if blocked := too_many("submit_return_tracking", mcp_ctx):
            return blocked
        return tools.submit_return_tracking(checkout_id, buyer_phone, waybill, shop=shop_for(mcp_ctx))

    return mcp
