"""MCP server: the tools a buyer's AI agent calls (search, shipping, checkout, status).

Served over streamable HTTP at /mcp/ by the FastAPI app. Any MCP client works, including
ADK's McpToolset (see agents/buyer_agent.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..security import RateLimiter, client_ip

if TYPE_CHECKING:
    from ..container import AppContext

INSTRUCTIONS = """Etalase lets you buy from independent Indonesian sellers who sell on WhatsApp.
Flow: search_products -> get_shipping_options -> create_checkout -> show the QRIS code -> get_checkout_status.
Only domestic delivery inside Indonesia is supported. Prices are in IDR.
If a variant's availability is 'confirm_with_seller', tell the buyer the seller confirms stock first."""


# (bucket, calls, window in seconds) per client address. The buckets are shared with the REST routes of the same name in
# api/main.py, so an agent cannot get around a limit by switching from REST to MCP. A shipping quote costs a courier-API
# call and a checkout holds stock for 15 minutes, which is why those two are the tightest.
TOOL_LIMITS = {
    "search_products": ("search", 120, 60),
    "get_product": ("search", 120, 60),
    "get_shipping_options": ("shipping", 60, 60),
    "create_checkout": ("checkout", 20, 3600),
    "get_checkout_status": ("status", 240, 60),
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

    @mcp.tool()
    def search_products(query: str, max_price_idr: int | None = None, ship_to_country: str = "ID",
                        limit: int = 5, mcp_ctx: Context = None) -> dict:
        """Search products from WhatsApp sellers. Query in English or Bahasa Indonesia, e.g. 'black voal hijab'."""
        if blocked := too_many("search_products", mcp_ctx):
            return blocked
        return tools.search_products(query, max_price_idr, ship_to_country, limit)

    @mcp.tool()
    def get_product(product_id: str, mcp_ctx: Context = None) -> dict:
        """Get one product with its variants, prices and availability."""
        if blocked := too_many("get_product", mcp_ctx):
            return blocked
        return tools.get_product(product_id)

    @mcp.tool()
    def get_shipping_options(product_id: str, variant_id: str, quantity: int, postal_code: str,
                             mcp_ctx: Context = None) -> dict:
        """Courier options and prices to the buyer's postal code."""
        if blocked := too_many("get_shipping_options", mcp_ctx):
            return blocked
        return tools.get_shipping_options(product_id, variant_id, quantity, postal_code)

    @mcp.tool()
    def create_checkout(product_id: str, variant_id: str, quantity: int, buyer_name: str, buyer_phone: str,
                        address_line: str, city: str, postal_code: str, country: str = "ID",
                        courier: str | None = None, mcp_ctx: Context = None) -> dict:
        """Reserve stock and start checkout. Returns a QRIS payment code, or a pending state while the seller confirms stock.
        Only call this after the buyer explicitly agreed to buy."""
        if blocked := too_many("create_checkout", mcp_ctx):
            return blocked
        return tools.create_checkout(product_id, variant_id, quantity, buyer_name, buyer_phone, address_line,
                                     city, postal_code, country, courier)

    @mcp.tool()
    def get_checkout_status(checkout_id: str, mcp_ctx: Context = None) -> dict:
        """Current status of a checkout, the payment code once available, and tracking after shipment."""
        if blocked := too_many("get_checkout_status", mcp_ctx):
            return blocked
        return tools.get_checkout_status(checkout_id)

    return mcp
