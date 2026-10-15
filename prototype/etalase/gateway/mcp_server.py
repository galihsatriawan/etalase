"""MCP server: the tools a buyer's AI agent calls (search, shipping, checkout, status).

Served over streamable HTTP at /mcp/ by the FastAPI app. Any MCP client works, including
ADK's McpToolset (see agents/buyer_agent.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

if TYPE_CHECKING:
    from ..container import AppContext

INSTRUCTIONS = """Etalase lets you buy from independent Indonesian sellers who sell on WhatsApp.
Flow: search_products -> get_shipping_options -> create_checkout -> show the QRIS code -> get_checkout_status.
Only domestic delivery inside Indonesia is supported. Prices are in IDR.
If a variant's availability is 'confirm_with_seller', tell the buyer the seller confirms stock first."""


def build_mcp(ctx: "AppContext") -> FastMCP:
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

    @mcp.tool()
    def search_products(query: str, max_price_idr: int | None = None, ship_to_country: str = "ID",
                        limit: int = 5) -> dict:
        """Search products from WhatsApp sellers. Query in English or Bahasa Indonesia, e.g. 'black voal hijab'."""
        return tools.search_products(query, max_price_idr, ship_to_country, limit)

    @mcp.tool()
    def get_product(product_id: str) -> dict:
        """Get one product with its variants, prices and availability."""
        return tools.get_product(product_id)

    @mcp.tool()
    def get_shipping_options(product_id: str, variant_id: str, quantity: int, postal_code: str) -> dict:
        """Courier options and prices to the buyer's postal code."""
        return tools.get_shipping_options(product_id, variant_id, quantity, postal_code)

    @mcp.tool()
    def create_checkout(product_id: str, variant_id: str, quantity: int, buyer_name: str, buyer_phone: str,
                        address_line: str, city: str, postal_code: str, country: str = "ID",
                        courier: str | None = None) -> dict:
        """Reserve stock and start checkout. Returns a QRIS payment code, or a pending state while the seller confirms stock.
        Only call this after the buyer explicitly agreed to buy."""
        return tools.create_checkout(product_id, variant_id, quantity, buyer_name, buyer_phone, address_line,
                                     city, postal_code, country, courier)

    @mcp.tool()
    def get_checkout_status(checkout_id: str) -> dict:
        """Current status of a checkout, the payment code once available, and tracking after shipment."""
        return tools.get_checkout_status(checkout_id)

    return mcp
