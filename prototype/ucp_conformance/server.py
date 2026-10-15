"""Run Etalase with fixed demo data, so the official UCP conformance suite can be pointed at it.

    python -m ucp_conformance.server --port 8190

The suite needs known item ids; Etalase normally generates them. This script seeds one verified seller with
three variants (a normal one, a sold-out one) under stable ids and then starts the real app, unchanged,
on the mock payment and shipping adapters.
"""

from __future__ import annotations

import argparse
import os


def seed(ctx) -> None:
    from etalase.models import Address, Product, ProductStatus, Seller, Stock, Variant
    seller = Seller(id="sel_conformance", name="Conformance Shop", whatsapp="6281200000000", kyc_status="verified",
                    origin=Address(name="Conformance Shop", line1="Jl. Contoh 1", city="Jakarta", postal_code="12950"))
    ctx.store.put_seller(seller)
    for pid, vid, title, stock in (("prd_conf_1", "item_1", "Conformance Item", 1000),
                                   ("prd_conf_2", "item_out_of_stock", "Sold Out Item", 0)):
        ctx.store.put_product(Product(
            id=pid, seller_id=seller.id, title=title, status=ProductStatus.PUBLISHED, readiness_score=100,
            variants=[Variant(id=vid, sku=vid.upper(), price=50_000, weight_gram=300,
                              stock=Stock(on_hand=stock, source="seed"))]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8190)
    args = ap.parse_args()
    os.environ["ETALASE_ENV"] = "dev"
    os.environ["PUBLIC_BASE_URL"] = f"http://localhost:{args.port}"
    import uvicorn

    from etalase.api.main import app, ctx
    seed(ctx)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
