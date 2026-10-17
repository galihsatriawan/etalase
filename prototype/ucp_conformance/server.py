"""Run Etalase with fixed demo data, so the official UCP conformance suite can be pointed at it.

    python -m ucp_conformance.server --port 8190               # production behaviour: QRIS is the only way to pay
    python -m ucp_conformance.server --port 8190 --test-mode   # the sandbox test mode the order tests need (UCP_TEST_MODE)

Test mode (gateway/ucp_order.py) accepts the suite's mock card token as a payment, lets the suite's mock platform (localhost) receive
signed order webhooks, and enables PUT /orders/{id} and /testing/simulate-shipping/{id}. It exists because the suite creates every
order by paying with a card, which Etalase's production flow never accepts. The suite also never sends a buyer, which Etalase needs
in order to ship, so in test mode the missing name and phone of a create request are filled in (PlaceholderBuyer, below).

The suite needs known item ids; Etalase normally generates them. This script seeds one verified seller with
two variants (an in-stock one that was just confirmed, so checkout is instant, and a sold-out one) under stable ids and then starts the real app, unchanged,
on the mock payment and shipping adapters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os


def seed(ctx) -> None:
    from etalase.models import Address, Product, ProductStatus, Seller, Stock, Variant, now
    seller = Seller(id="sel_conformance", name="Conformance Shop", whatsapp="6281200000000", kyc_status="verified",
                    origin=Address(name="Conformance Shop", line1="Jl. Contoh 1", city="Jakarta", postal_code="12950"))
    ctx.store.put_seller(seller)
    for pid, vid, title, stock in (("prd_conf_1", "item_1", "Conformance Item", 1000),
                                   ("prd_conf_2", "item_out_of_stock", "Sold Out Item", 0)):
        ctx.store.put_product(Product(
            id=pid, seller_id=seller.id, title=title, status=ProductStatus.PUBLISHED, readiness_score=100,
            variants=[Variant(id=vid, sku=vid.upper(), price=50_000, weight_gram=300,
                              stock=Stock(on_hand=stock, source="seed", last_confirmed_at=now()))]))


class PlaceholderBuyer:
    """Test mode only: give a new checkout session the buyer details Etalase needs to ship, when the request lacks them.

    Etalase needs the buyer's name and phone number (the courier and the seller use them) and says so with a recoverable error until
    they are there. The official suite sends no buyer, or only an e-mail address and a display name, so none of its sessions could
    ever be completed. This wrapper sits in front of the app, outside the product code, and fills in the missing name and phone of a
    *create* request. The phone number comes from the Idempotency-Key, so a retried create is the same request, and differs between
    creates, because Etalase limits checkouts per phone number per hour. Updates and completes pass through unchanged."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def fill(data: dict, key: str) -> dict:
        buyer = dict(data.get("buyer") or {})
        buyer.setdefault("first_name", (buyer.get("name") or "Conformance").split(" ")[0])
        buyer.setdefault("last_name", "Buyer")
        buyer.setdefault("phone_number", "62857" + f"{int(hashlib.sha256(key.encode()).hexdigest(), 16) % 10**8:08d}")
        return {**data, "buyer": buyer}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/checkout-sessions"):
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        key = dict(scope["headers"]).get(b"idempotency-key", b"").decode() or body.decode("utf-8", "replace")
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                body = json.dumps(self.fill(data, key)).encode()
        except ValueError:
            pass            # not JSON: the app answers with its own error
        headers = [(k, v) for k, v in scope["headers"] if k != b"content-length"] + [(b"content-length", str(len(body)).encode())]
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app({**scope, "headers": headers}, replay, send)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--test-mode", action="store_true")
    ap.add_argument("--simulation-secret", default="conformance-sim-secret")
    ap.add_argument("--agent-port", type=int, default=8285, help="where the suite's mock platform serves its profile")
    args = ap.parse_args()
    os.environ["ETALASE_ENV"] = "dev"
    os.environ["PUBLIC_BASE_URL"] = f"http://localhost:{args.port}"
    if args.test_mode:
        os.environ["UCP_TEST_MODE"] = "true"
        os.environ["UCP_SIMULATION_SECRET"] = args.simulation_secret
        os.environ["UCP_PLATFORMS"] = json.dumps([f"http://localhost:{args.agent_port}/profiles/shopping-agent.json"])
    import uvicorn

    from etalase.api.main import app, ctx
    seed(ctx)
    uvicorn.run(PlaceholderBuyer(app) if args.test_mode else app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
