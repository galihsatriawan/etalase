"""Discovery documents for agents and crawlers.

- /.well-known/ucp lives in gateway/ucp.py (the real Universal Commerce Protocol profile).
- /stores/{seller_id}/products.jsonld: schema.org Product data per store (also the basis
  for a Merchant Center feed).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..container import AppContext


def return_policy_jsonld(ctx: "AppContext", seller) -> dict:
    """schema.org MerchantReturnPolicy for a seller (property names checked against schema.org on 2026-10-15)."""
    base = ctx.settings.public_base_url.rstrip("/")
    policy = ctx.returns.policy_view(seller)
    if not policy["window_hours"]:
        return {"@type": "MerchantReturnPolicy", "applicableCountry": "ID",
                "returnPolicyCategory": "https://schema.org/MerchantReturnNotPermitted"}
    out = {
        "@type": "MerchantReturnPolicy", "applicableCountry": "ID",
        "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
        "merchantReturnDays": math.ceil(policy["window_hours"] / 24),
        "returnMethod": "https://schema.org/ReturnByMail",
        # The buyer sends the item back and pays that shipping; Etalase does not reimburse it in this version.
        "itemDefectReturnFees": "https://schema.org/ReturnShippingFees",
        "merchantReturnLink": f"{base}/stores/{seller.id}/returns",
        "description": ("Damaged, wrong or not-as-described items are refunded in full (the shipping fee paid at checkout included)" +
                        ("; items the buyer no longer wants are refunded the product price only, without the original shipping fee."
                         if seller.return_policy.accepts_change_of_mind else "; a change of mind is not accepted.")
                        + " The buyer pays the return shipping."),
    }
    if seller.return_policy.accepts_change_of_mind:
        out["customerRemorseReturnFees"] = "https://schema.org/ReturnShippingFees"
    else:
        out["refundType"] = "https://schema.org/FullRefund"     # with a change of mind the refund is partial and schema.org has no value for that
    return out


def schema_org_products(ctx: "AppContext", seller_id: str) -> list[dict]:
    base = ctx.settings.public_base_url.rstrip("/")
    seller = ctx.store.get_seller(seller_id)
    policy = return_policy_jsonld(ctx, seller) if seller else None
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
                    **({"hasMerchantReturnPolicy": policy} if policy else {}),
                },
            })
    return out
