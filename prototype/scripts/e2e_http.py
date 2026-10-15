#!/usr/bin/env python3
"""End to end over real HTTP: starts the real app (uvicorn, mock adapters, in-memory store), then plays the whole story.

    python scripts/e2e_http.py            # starts its own server on a free port
    python scripts/e2e_http.py URL        # or test a server that is already running (ETALASE_ENV=dev)

Seller builds a catalog on WhatsApp -> an agent finds it over REST -> buys it over UCP (profile, create, update, courier,
complete, hosted QRIS page) -> payment webhook -> seller accepts and ships on WhatsApp -> courier webhook -> payout;
then a second agent sees the shop over MCP (streamable HTTP, real client). Exit code 1 on the first failed step.
Providers are mocks: this proves our own wiring over real sockets, not Meta, Xendit, Biteship or Gemini.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        raise SystemExit(1)


def start_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "ETALASE_ENV": "dev", "PUBLIC_BASE_URL": base}
    log = open(os.devnull, "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "etalase.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log)   # clean env and no .env on purpose: keys must not leak in
    for _ in range(60):
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("the server did not start")


def story(BASE):
    UCP = {"UCP-Agent": 'profile="https://platform.example/profile"'}
    SELLER = "6281234567801"
    http = httpx.Client(base_url=BASE, timeout=20)
    # 1 seller onboards on WhatsApp and builds a catalog from one chat message
    seller = http.post("/dev/sellers", json={"name": "Toko Rina", "whatsapp": SELLER}).json()
    r = http.post("/dev/whatsapp", json={"sender": SELLER, "text": "Hijab voal, hitam 5, navy 3, mocca 10, harga 85rb"}).json()
    ok("seller message -> draft with Publish button", any(b for m in r for b in (m["buttons"] or [])), r[-1]["text"][:70].replace("\n", " "))
    r = http.post("/dev/whatsapp", json={"sender": SELLER, "button_id": "publish"}).json()
    ok("tap Publish -> product is live", "publish" in r[-1]["text"].lower() or "live" in r[-1]["text"].lower() or "terbit" in r[-1]["text"].lower(), r[-1]["text"][:70].replace("\n", " "))

    # 2 an AI agent finds it over REST and reads shipping options
    found = http.get("/agent/v1/search", params={"q": "hijab voal"}).json()
    hit = (found.get("results") or found.get("products") or found)[0]
    ok("agent search finds the product", "voal" in json.dumps(hit).lower(), hit["title"])
    detail = http.get(f"/agent/v1/products/{hit['product_id']}").json()
    black = next(v for v in detail["variants"] if v["options"].get("color") == "Black")
    vid = black["variant_id"]
    ok("variant found with price and stock", black["price"] == 85000 and black["availability"] == "in_stock", f"{black['options']} {black['price']} {black['availability']}")

    # 3 the same product over UCP: profile -> create -> update -> select courier -> complete
    profile = http.get("/.well-known/ucp").json()["ucp"]
    ok("UCP profile advertises checkout + fulfillment", {"dev.ucp.shopping.checkout", "dev.ucp.shopping.fulfillment"} <= set(profile["capabilities"]))
    s = http.post("/ucp/v1/checkout-sessions", headers=UCP, json={"line_items": [{"item": {"id": vid}, "quantity": 2}]})
    ok("UCP create -> 201 incomplete", s.status_code == 201 and s.json()["status"] == "incomplete", s.json()["status"])
    s = s.json()
    buyer = {"first_name": "Dewi", "last_name": "Lestari", "phone_number": "6285700112233"}
    dest = {"street_address": "Jl. Dago 10", "address_locality": "Bandung", "address_region": "Jawa Barat", "postal_code": "40135", "address_country": "ID"}
    s = http.put(f"/ucp/v1/checkout-sessions/{s['id']}", headers=UCP, json={"line_items": [{"id": "li_1", "item": {"id": vid}, "quantity": 2}], "buyer": buyer,
            "fulfillment": {"methods": [{"type": "shipping", "destinations": [dest]}]}}).json()
    opt = s["fulfillment"]["methods"][0]["groups"][0]["options"][0]
    s = http.put(f"/ucp/v1/checkout-sessions/{s['id']}", headers=UCP, json={"buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [dest],
            "groups": [{"selected_option_id": opt["id"]}]}]}}).json()
    ok("UCP update -> ready_for_complete with total", s["status"] == "ready_for_complete", f"total {s['totals'][-1]['amount']/100:,.0f} IDR")
    done = http.post(f"/ucp/v1/checkout-sessions/{s['id']}/complete", headers=UCP, json={"payment": {"instruments": []}, **{}}).json() if False else http.post(f"/ucp/v1/checkout-sessions/{s['id']}/complete", headers=UCP, json={}).json()
    ok("UCP complete -> requires_escalation with continue_url (pay by QRIS)", done["status"] == "requires_escalation" and done["continue_url"].startswith(BASE), done["continue_url"])
    page = http.get(done["continue_url"].replace(BASE, ""))
    ok("hosted payment page shows a QR and the amount", page.status_code == 200 and "<svg" in page.text and "Rp" in page.text)
    ok("page is locked down by CSP", "default-src 'none'" in page.headers["content-security-policy"])

    # 4 seller got the order on WhatsApp; buyer pays (simulated QRIS callback); status flips to completed
    http.post("/auth/otp", json={"whatsapp": SELLER})
    import re
    code = [m for m in http.get("/dev/outbox", params={"to": SELLER}).json() if "sign-in code" in m["text"].lower()][-1]["text"]
    digits = re.search(r"\b(\d{6})\b", code).group(1)
    tok = http.post("/auth/verify", json={"whatsapp": SELLER, "code": digits})
    ok("seller signs in with the WhatsApp one-time code", tok.status_code == 200)
    orders = http.get("/api/me/orders", headers={"Authorization": f"Bearer {tok.json()['token']}"}).json()
    ok("dashboard lists exactly one order, stock reserved", len(orders) == 1 and orders[0]["status"] == "awaiting_payment", orders[0]["status"])
    paid = http.post(f"/dev/pay/{orders[0]['checkout_id']}").json()
    ok("payment webhook -> order paid", paid["status"] in ("paid", "accepted"), paid["status"])
    final = http.get(f"/ucp/v1/checkout-sessions/{s['id']}", headers=UCP).json()
    ok("UCP session now completed with the order id", final["status"] == "completed" and final["order"]["id"] == orders[0]["checkout_id"])
    seller_msgs = [m["text"] for m in http.get("/dev/outbox", params={"to": SELLER}).json()]
    ok("seller was notified on WhatsApp", any("85" in t or "order" in t.lower() or "pesanan" in t.lower() for t in seller_msgs), f"{len(seller_msgs)} messages")
    oid = orders[0]["checkout_id"]
    http.post("/dev/whatsapp", json={"sender": SELLER, "button_id": f"accept:{oid}"})
    r = http.post("/dev/whatsapp", json={"sender": SELLER, "text": "shipped, tracking JNE123456"}).json()
    status = http.get(f"/agent/v1/checkouts/{oid}").json()["status"]
    ok("seller accepts, then ships with a tracking number", status == "shipped", status)
    http.post("/webhooks/shipping", json={"waybill": "JNE123456", "status": "delivered"})
    status = http.get(f"/agent/v1/checkouts/{oid}").json()["status"]
    ok("courier webhook -> delivered and paid out", status in ("delivered", "paid_out"), status)
    orders = http.get("/api/me/orders", headers={"Authorization": f"Bearer {tok.json()['token']}"}).json()
    pay = orders[0]["payout"]
    ok("payout released: 4% commission on the goods, the rest to the seller",
       pay["status"] == "released" and pay["commission"] == round(orders[0]["subtotal"] * 0.04)
       and pay["seller_amount"] + pay["commission"] == orders[0]["subtotal"], json.dumps(pay))
    ok("UCP session still reads completed after delivery", http.get(f"/ucp/v1/checkout-sessions/{s['id']}", headers=UCP).json()["status"] == "completed")

    # 5 a second agent over MCP sees the same shop (streamable HTTP, real client library)
    async def mcp_part():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(f"{BASE}/mcp/") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                res = await session.call_tool("search_products", {"query": "hijab voal"})
                return tools, res
    tools, res = asyncio.run(mcp_part())
    ok("MCP lists the agent tools", {"search_products", "create_checkout"} <= set(tools), ", ".join(tools))
    ok("MCP search_products returns the shop's product", "voal" in json.dumps([c.text for c in res.content]).lower())
    print("\nALL END-TO-END STEPS PASSED")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        story(sys.argv[1].rstrip("/"))
    else:
        proc, base = start_server()
        try:
            story(base)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
