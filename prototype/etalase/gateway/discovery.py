"""Discovery documents for agents and crawlers.

- /.well-known/ucp: a simplified, UCP-style business profile pointing agents at our MCP
  and REST endpoints. The field names here are illustrative; align them with the published
  Universal Commerce Protocol specification before production.
- /stores/{seller_id}/products.jsonld: schema.org Product data per store (also the basis
  for a Merchant Center feed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..container import AppContext


def ucp_profile(ctx: "AppContext") -> dict:
    base = ctx.settings.public_base_url.rstrip("/")
    return {
        "name": "Etalase",
        "description": "Independent Indonesian WhatsApp & Instagram sellers, shoppable by AI agents.",
        "profile_version": "draft",
        "services": {
            "mcp": {"transport": "streamable-http", "endpoint": f"{base}/mcp/"},
            "rest": {"endpoint": f"{base}/agent/v1"},
        },
        "capabilities": ["catalog.search", "catalog.get", "shipping.quote", "checkout.create", "checkout.status"],
        "markets": [{"country": "ID", "currency": "IDR", "delivery": "domestic"}],
        "payment_methods": ["qris"],
        "policies": {"stock": "held for 15 minutes during checkout; some sellers confirm stock first"},
    }


def schema_org_products(ctx: "AppContext", seller_id: str) -> list[dict]:
    base = ctx.settings.public_base_url.rstrip("/")
    seller = ctx.store.get_seller(seller_id)
    out = []
    for p in ctx.store.list_products(seller_id=seller_id, status="published"):
        for v in p.variants:
            avail = v.stock.available
            out.append({
                "@context": "https://schema.org", "@type": "Product",
                "sku": v.sku, "name": f"{p.title} {v.label}".strip(), "description": p.description,
                "category": p.category, "image": [ctx.catalog.image_url(i) for i in p.images],
                "brand": {"@type": "Brand", "name": seller.name if seller else ""},
                "weight": {"@type": "QuantitativeValue", "value": v.weight_gram, "unitCode": "GRM"},
                "offers": {
                    "@type": "Offer", "price": v.price, "priceCurrency": "IDR",
                    "availability": ("https://schema.org/OutOfStock" if avail == 0 else
                                     "https://schema.org/LimitedAvailability" if avail is None else
                                     "https://schema.org/InStock"),
                    "url": f"{base}/agent/v1/products/{p.id}",
                    "shippingDetails": {"@type": "OfferShippingDetails",
                                        "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "ID"}},
                },
            })
    return out
