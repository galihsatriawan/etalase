"""End-to-end demo with no credentials: seller onboarding on WhatsApp (and optional Instagram import), an AI agent buying over
MCP, payment, shipping, payout, a confirm-first order, an oversell race and the Connector API.

    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ETALASE_ENV", "dev")

from fastapi.testclient import TestClient  # noqa: E402

from etalase.api.main import app, ctx, mcp  # noqa: E402
from etalase.seller.format import rupiah  # noqa: E402

import logging  # noqa: E402

logging.disable(logging.INFO)  # keep the demo output readable
for noisy in ("httpx", "mcp", "etalase"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

SELLER_WA = "6281234567890"
BUYER = {"buyer_name": "Dewi", "buyer_phone": "6285700001111", "address_line": "Jl. Dago No. 10",
         "city": "Bandung", "postal_code": "40135"}


def step(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show_replies(replies: list[dict]) -> None:
    for r in replies:
        print("  [WhatsApp → seller]")
        for line in r["text"].splitlines():
            print(f"   {line}")
        if r["buttons"]:
            print("   " + "  ".join(f"[{title}]" for _, title in r["buttons"]))


def tool(name: str, **args) -> dict:
    content = asyncio.run(mcp.call_tool(name, args))
    result = json.loads(content[0].text)
    print(f"  [agent → MCP] {name}({', '.join(f'{k}={v!r}' for k, v in args.items() if k not in BUYER)})")
    return result


def main() -> None:
    with TestClient(app) as client:
        wa = lambda **body: client.post("/dev/whatsapp", json={"sender": SELLER_WA, **body}).json()  # noqa: E731
        print("Modes:", ctx.modes)

        step("1. A seller signs up (KYC is handled by the payment provider; mocked here)")
        seller = client.post("/dev/sellers", json={"name": "Toko Rina", "whatsapp": SELLER_WA,
                                                   "city": "Jakarta", "postal_code": "12950"}).json()
        print(f"  seller {seller['id']} · {seller['name']} · origin {seller['origin']['city']}")

        step("2. The seller sends a product photo, then a description, on WhatsApp")
        show_replies(wa(image_path=str(ROOT / "samples" / "voal-hijab.png")))
        print("  [seller → WhatsApp] Voal hijab, black 5, navy 3, mocca 10, price 85k")
        show_replies(wa(text="Voal hijab, black 5, navy 3, mocca 10, price 85k"))
        print("  [seller taps] Publish")
        show_replies(wa(button_id="publish"))

        step("2b. The same seller connects Instagram: product posts become catalog drafts")
        r = client.post("/dev/instagram/import", params={"seller_id": seller["id"]}).json()
        show_replies(r["replies"])
        print("  [seller taps] Publish")
        show_replies(wa(button_id="publish"))

        step("3. A buyer asks their AI agent; the agent shops through Etalase's MCP server")
        print('  [buyer → agent] "Find a black voal hijab under Rp100k, ship to Bandung 40135"')
        found = tool("search_products", query="black voal hijab", max_price_idr=100000)
        hit = found["results"][0]
        variant = hit["variants"][0]
        print(f"  → {hit['title']} ({variant['options']['color']}) from {hit['seller']['name']}: "
              f"{rupiah(variant['price'])} · {variant['availability']}")
        rates = tool("get_shipping_options", product_id=hit["product_id"], variant_id=variant["variant_id"],
                     quantity=2, postal_code=BUYER["postal_code"])
        cheapest = rates["options"][0]
        print(f"  → cheapest courier {cheapest['courier'].upper()} {cheapest['service']} {rupiah(cheapest['price'])} ({cheapest['etd']})")
        print('  [buyer → agent] "Yes, buy 2"')
        co = tool("create_checkout", product_id=hit["product_id"], variant_id=variant["variant_id"], quantity=2, **BUYER)
        print(f"  → checkout {co['checkout_id']} · {co['status']} · total {rupiah(co['total'])}")
        print(f"  → QRIS: {co['payment']['qr_string'][:48]}…  (stock held until {co['expires_at'][11:19]} UTC)")

        step("4. The buyer pays; the payment webhook reaches Etalase; the seller accepts in WhatsApp")
        ref = ctx.store.get_order(co["checkout_id"]).payment.reference
        start = len(ctx.messenger.outbox)
        client.post("/webhooks/payment", json={"reference": ref, "status": "paid"})
        show_replies([{"text": m.text, "buttons": m.buttons} for m in ctx.messenger.outbox[start:]])
        print("  [seller taps] Accept")
        show_replies(wa(button_id=f"accept:{co['checkout_id']}"))

        step("5. The seller ships and replies with the tracking number")
        print("  [seller → WhatsApp] shipped, tracking JNE123456")
        show_replies(wa(text="shipped, tracking JNE123456"))
        print(f"  [agent → MCP] get_checkout_status → {tool('get_checkout_status', checkout_id=co['checkout_id'])['next_step']}")

        step("6. The courier says delivered → the payout waits out the return window, then it is released")
        start = len(ctx.messenger.outbox)
        client.post("/webhooks/shipping", json={"waybill": "JNE123456", "status": "delivered"})
        show_replies([{"text": m.text, "buttons": m.buttons} for m in ctx.messenger.outbox[start:]])
        status = tool("get_checkout_status", checkout_id=co["checkout_id"])
        print(f"  → the agent is told: {status['next_step']}")
        print(f"  → payout so far: {ctx.store.get_order(co['checkout_id']).payout} (held at the payment provider)")
        print("  … the return window passes (dev clock) …")
        start = len(ctx.messenger.outbox)
        client.post(f"/dev/release/{co['checkout_id']}")
        show_replies([{"text": m.text, "buttons": m.buttons} for m in ctx.messenger.outbox[start:]])

        step("6b. A second order arrives damaged: the buyer's agent returns it, the seller approves, the buyer is refunded")
        second = tool("create_checkout", product_id=hit["product_id"], variant_id=variant["variant_id"], quantity=1, **BUYER)
        client.post("/dev/pay/" + second["checkout_id"])
        wa(button_id=f"accept:{second['checkout_id']}")
        wa(text="tracking JNE222222")
        client.post("/webhooks/shipping", json={"waybill": "JNE222222", "status": "delivered"})
        start = len(ctx.messenger.outbox)
        asked = tool("request_return", checkout_id=second["checkout_id"], buyer_phone=BUYER["buyer_phone"], reason="damaged",
                     note="The seam is torn", evidence_urls=["https://img.example/torn.jpg"])
        print(f"  → {asked['status']} · {asked['next_step']}")
        show_replies([{"text": m.text, "buttons": m.buttons} for m in ctx.messenger.outbox[start:]])
        print("  [seller taps] Approve return, then Item received")
        show_replies(wa(button_id=f"return_ok:{second['checkout_id']}"))
        wa(button_id=f"return_got:{second['checkout_id']}")
        final = tool("get_checkout_status", checkout_id=second["checkout_id"])
        print(f"  → {final['status']} · {final['next_step']}  (no payout was ever released for it)")

        step("7. Stock by chat, then a confirm-first order (low stock → the seller confirms before payment)")
        print("  [seller → WhatsApp] navy sold out")
        show_replies(wa(text="navy sold out"))
        print("  [seller → WhatsApp] mocca 2 left")
        show_replies(wa(text="mocca 2 left"))
        found = tool("search_products", query="mocca voal hijab")
        mv = found["results"][0]["variants"][0]
        print(f"  → Mocca availability: {mv['availability']} (sell mode {mv['sell_mode']})")
        start = len(ctx.messenger.outbox)
        co2 = tool("create_checkout", product_id=found["results"][0]["product_id"], variant_id=mv["variant_id"],
                   quantity=1, **BUYER)
        print(f"  → checkout {co2['checkout_id']} · {co2['status']} · payment: {co2['payment']}")
        show_replies([{"text": m.text, "buttons": m.buttons} for m in ctx.messenger.outbox[start:]])
        print("  [seller taps] Available")
        show_replies(wa(button_id=f"confirm:{co2['checkout_id']}"))
        status = tool("get_checkout_status", checkout_id=co2["checkout_id"])
        print(f"  → now {status['status']}, QRIS ready: {bool(status['payment'] and status['payment']['qr_string'])}")

        step("8. Oversell race: 50 agents try to buy the last unit at the same time")
        last = next(p for p in ctx.store.list_products(seller_id=seller["id"]) if p.title == "Voal Hijab")
        black = next(v for v in last.variants if v.options.get("color") == "Black")
        ctx.store.set_stock(seller["id"], last.id, black.id, 1, "demo")
        black_now = ctx.store.get_product(last.id).variant(black.id)
        print(f"  Black stock set to {black_now.stock.on_hand} (reserved {black_now.stock.reserved})")
        wins, out_of_stock, lock = [], [], threading.Lock()

        def attempt(i):  # 50 different buyers, so per-buyer limits do not interfere
            r = ctx.gateway.create_checkout(last.id, black.id, 1, **(BUYER | {"buyer_phone": f"62857{i:08d}"}))
            with lock:
                (out_of_stock if "not enough stock" in r.get("error", "") else wins).append(r)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(50)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        print(f"  → successful checkouts: {len(wins)} of 50 (expected 1), refused as out of stock: {len(out_of_stock)}. "
              f"Oversold units: {max(0, len(wins) - 1)}")

        step("9. Seller dashboard sign-in with a WhatsApp one-time code")
        client.post("/auth/otp", json={"whatsapp": SELLER_WA})
        code = ctx.messenger.outbox[-1].text.split(": ")[1][:6]
        token = client.post("/auth/verify", json={"whatsapp": SELLER_WA, "code": code}).json()["token"]
        orders = client.get("/api/me/orders", headers={"authorization": f"Bearer {token}"}).json()
        for o in orders:
            payout = f" · payout {rupiah(o['payout']['seller_amount'])}" if o["payout"] else ""
            print(f"  {o['checkout_id']} · {o['status']}{payout}")

        step("10. Connector API: a website builder pushes a store once; agents can buy from it")
        builder_seller = client.post("/dev/sellers", json={"name": "Kopi Lereng", "whatsapp": "6281300002222",
                                                           "city": "Malang", "postal_code": "65111"}).json()
        conn = client.post("/dev/connections", params={"seller_id": builder_seller["id"],
                                                       "order_webhook_url": "test://builder/orders"}).json()
        key = {"X-Etalase-Key": conn["api_key"]}
        r = client.put("/connector/v1/products/sku-arabica-250", headers=key, json={
            "title": "Arabica Coffee Beans 250 g", "description": "Single-origin arabica from Malang, medium roast.",
            "category": "Food & Drink > Coffee", "images": ["https://example.com/arabica.jpg"],
            "variants": [{"external_id": "v1", "sku": "ARA-250", "options": {"grind": "Whole bean"},
                          "price": 95000, "weight_gram": 300, "stock": 40}]}).json()
        print(f"  PUT /connector/v1/products → {r['status']}, readiness {r['readiness']}/100")
        print(f"  PATCH /connector/v1/stock → {client.patch('/connector/v1/stock', headers=key, json=[{'sku': 'ARA-250', 'stock': 35}]).json()}")
        found = tool("search_products", query="arabica coffee")
        print(f"  → agent finds: {found['results'][0]['title']} from {found['results'][0]['seller']['name']}")
        print(f"  OpenAPI spec for builders: GET /openapi.json ({len(client.get('/openapi.json').json()['paths'])} paths)")

        step("Done")
        print(f"  Events published: {len(ctx.events.published)} · WhatsApp messages sent: {len(ctx.messenger.outbox)}")


if __name__ == "__main__":
    main()
