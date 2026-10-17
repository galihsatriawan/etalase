#!/usr/bin/env python3
"""The reviewer's path through the judge sandbox, over real HTTP, exactly as the instructions in JUDGES.md describe it.

    python scripts/e2e_judge.py            # starts its own server on a free port with DEMO_MODE=true
    python scripts/e2e_judge.py URL        # or test a sandbox that is already running (the deployed one)

A visitor opens the simulated seller phone, sends a sample photo and a description, publishes, buys as a person on the shop page,
pays with the demo button, and runs the order to the payout with the buttons in the chat and the console; then buys the
nearly sold-out item (the seller is asked first) and checks that an agent's checkout page points to the same button.
It also checks the edges: the console can only touch the demo seller's orders, and a shop that is not the demo shop gets no button.
Providers are mocks: this proves our own wiring, not Meta, Xendit, Biteship or Gemini. Exit code 1 on the first failed step.
"""
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
SELLER_ID = "sel_demo"


def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        raise SystemExit(1)


def start_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "ETALASE_ENV": "dev", "PUBLIC_BASE_URL": base,
           "DEMO_MODE": "true"}
    log = open(os.devnull, "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "etalase.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log)
    for _ in range(60):
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("the server did not start")


FORM = {"name": "Dewi", "phone": "0857 0011 2233", "address": "Jl. Dago 10", "city": "Bandung", "postal": "40135", "qty": "1"}


def buy(http, variant_id, **override):
    """The shop page's own form. Returns the order id taken from the page the buyer lands on."""
    r = http.post(f"/stores/{SELLER_ID}/buy/{variant_id}", data={**FORM, **override}, follow_redirects=True)
    m = re.search(r"/pay/(ord_[0-9a-f]+)", str(r.url))
    ok("the buy form leads to an order page", r.status_code == 200 and bool(m), str(r.url))
    return m.group(1), r.text


def state(http):
    return http.get("/demo/api/state").json()


def last_bot(http):
    return [m for m in state(http)["chat"] if m["who"] == "etalase"][-1]


def tap(http, button_id, label="tap"):
    return http.post("/demo/api/send", json={"button_id": button_id, "text": label}).json()


def story(base):
    http = httpx.Client(base_url=base, timeout=30)

    # 1 the pages a reviewer is given
    page = http.get("/demo")
    ok("the simulated seller phone opens", page.status_code == 200 and "Demo sandbox" in page.text)
    s = state(http)
    ok("it offers sample photos, never an upload", s["photos"] == ["voal-hijab.png", "pashmina-silk.png", "kemeja-linen.png"])
    ok("the dashboard has the demo sign-in", "Sign in as the demo seller" in http.get("/").text)
    token = http.post("/demo/api/signin").json()["token"]
    me = http.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    ok("sign-in as the demo seller reaches the dashboard API", me["id"] == SELLER_ID and me["kyc"] == "verified", me["name"])
    store = http.get(f"/stores/{SELLER_ID}")
    ok("the shop already has products", store.status_code == 200 and "Voal Hijab" in store.text and "Linen Shirt" in store.text)
    found = http.get("/agent/v1/search", params={"q": "voal hijab black"}).json()
    hits = found.get("results") or found.get("products") or found
    ok("an agent can find them without any setup", any("Voal" in h["title"] for h in hits), hits[0]["title"])

    # 2 the seller builds a catalog: a sample photo and a description, then Publish
    http.post("/demo/api/send", json={"photo": "pashmina-silk.png", "text": "Plain scarf, grey 6, olive 4, price 60k"})
    draft = last_bot(http)
    ok("photo + description -> a draft with a Publish button", any(b["id"] == "publish" for b in draft["buttons"]), draft["text"][:60].replace("\n", " "))
    bad = http.post("/demo/api/send", json={"photo": "../../etc/passwd"})
    ok("a photo outside the sample list is refused", bad.status_code == 400)
    tap(http, "publish", "Publish")
    ok("tapping Publish gives the shop link", f"/stores/{SELLER_ID}" in last_bot(http)["text"])

    # 3 a person buys on the shop page (an instant-sale item), and pays with the demo button
    order_id, order_page = buy(http, "var_demo_voal_black")
    ok("the order page shows the QR and the demo payment button", "<svg" in order_page and "Simulate the QRIS payment" in order_page)
    paid = http.post(f"/demo/pay/{order_id}", follow_redirects=True)
    ok("the demo button marks the order paid", paid.status_code == 200 and "Paid" in paid.text and "Simulate the QRIS payment" not in paid.text)
    notice = last_bot(http)
    ok("the seller's chat gets 'new paid order' with an Accept button", any(b["id"] == f"accept:{order_id}" for b in notice["buttons"]),
       notice["text"][:60].replace("\n", " "))

    # 4 the seller runs the order from the chat; the console stands in for the courier and the calendar
    tap(http, f"accept:{order_id}", "Accept")
    http.post("/demo/api/send", json={"text": "shipped, tracking JNE123456"})
    order = next(o for o in state(http)["orders"] if o["id"] == order_id)
    ok("accept + tracking number -> shipped, and the console offers the courier step", order["status"] == "shipped"
       and [a["id"] for a in order["actions"]] == ["deliver"], order["status"])
    r = http.post(f"/demo/api/orders/{order_id}/release")
    ok("the return window cannot be skipped before delivery", r.status_code == 409, r.json()["detail"])
    http.post(f"/demo/api/orders/{order_id}/deliver")
    order = next(o for o in state(http)["orders"] if o["id"] == order_id)
    ok("courier delivers -> the return window is open", order["status"] == "delivered" and [a["id"] for a in order["actions"]] == ["release"])
    ok("the buyer can ask for a return meanwhile", "Ask for a return" in http.get(f"/pay/{order_id}").text)
    http.post(f"/demo/api/orders/{order_id}/release")
    order = next(o for o in state(http)["orders"] if o["id"] == order_id)
    ok("window skipped -> paid out", order["status"] == "paid out" and order["actions"] == [], order["status"])

    # 5 the nearly sold-out item: the seller is asked before the buyer can pay
    cream = http.get("/agent/v1/search", params={"q": "silk pashmina"}).json()
    cream = json.dumps(cream)
    ok("the scarce colour is offered as 'confirm with seller'", "confirm_with_seller" in cream)
    order_id2, page2 = buy(http, "var_demo_pashmina_cream")
    ok("the buyer waits for the seller", "Waiting for the seller" in page2 and "Simulate the QRIS payment" not in page2)
    ask = last_bot(http)
    ok("the chat asks 'is it still available?' with a confirm button", any(b["id"] == f"confirm:{order_id2}" for b in ask["buttons"]),
       ask["text"][:60].replace("\n", " "))
    tap(http, f"confirm:{order_id2}", "Yes, available")
    ok("after the seller confirms, the payment button appears", "Simulate the QRIS payment" in http.get(f"/pay/{order_id2}").text)

    # 6 an agent's checkout: the hosted page cannot post forms, so it points to the order page
    UCP = {"UCP-Agent": 'profile="https://platform.example/profile"'}
    sess = http.post("/ucp/v1/checkout-sessions", headers=UCP, json={"line_items": [{"item": {"id": "var_demo_linen_m"}, "quantity": 1}]}).json()
    buyer = {"first_name": "Dewi", "last_name": "Lestari", "phone_number": "6285700112233"}
    dest = {"street_address": "Jl. Dago 10", "address_locality": "Bandung", "address_region": "Jawa Barat", "postal_code": "40135", "address_country": "ID"}
    sess = http.put(f"/ucp/v1/checkout-sessions/{sess['id']}", headers=UCP, json={"line_items": [{"id": "li_1", "item": {"id": "var_demo_linen_m"}, "quantity": 1}],
                    "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [dest]}]}}).json()
    opt = sess["fulfillment"]["methods"][0]["groups"][0]["options"][0]
    sess = http.put(f"/ucp/v1/checkout-sessions/{sess['id']}", headers=UCP, json={"buyer": buyer, "fulfillment": {"methods": [
        {"type": "shipping", "destinations": [dest], "groups": [{"selected_option_id": opt["id"]}]}]}}).json()
    done = http.post(f"/ucp/v1/checkout-sessions/{sess['id']}/complete", headers=UCP, json={"payment": {"instruments": []}}).json()
    hosted = http.get(done["continue_url"].replace(base, ""))
    ok("an agent's QRIS page points to the demo payment button", hosted.status_code == 200 and "simulate" in hosted.text.lower()
       and re.search(r'href="[^"]*/pay/ord_', hosted.text) is not None)

    # 7 the edges: the console and the button only ever touch the demo seller's orders
    other = http.post("/dev/sellers", json={"name": "Someone Else", "whatsapp": "6281234567855"}).json()
    http.post("/dev/whatsapp", json={"sender": "6281234567855", "text": "Kaos polos, putih 9, hitam 9, harga 75rb"})
    http.post("/dev/whatsapp", json={"sender": "6281234567855", "button_id": "publish"})
    shirt = http.get("/agent/v1/search", params={"q": "kaos polos"}).json()
    shirt = (shirt.get("results") or shirt.get("products") or shirt)[0]
    variant = http.get(f"/agent/v1/products/{shirt['product_id']}").json()["variants"][0]["variant_id"]
    foreign = http.post(f"/stores/{other['id']}/buy/{variant}", data=FORM, follow_redirects=True)
    foreign_id = re.search(r"/pay/(ord_[0-9a-f]+)", str(foreign.url)).group(1)
    ok("another seller's order page has no demo button", foreign.status_code == 200 and "Simulate the QRIS payment" not in foreign.text)
    ok("the console refuses another seller's order", http.post(f"/demo/api/orders/{foreign_id}/pay").status_code == 404)
    http.post(f"/demo/pay/{foreign_id}")
    ok("and so does the order-page button: the order stays unpaid", "Simulate the QRIS payment" not in http.get(f"/pay/{foreign_id}").text
       and "Paid" not in http.get(f"/pay/{foreign_id}").text.split("Order summary")[0])
    ok("an unknown order is a 404 from the console", http.post("/demo/api/orders/ord_nope/pay").status_code == 404)
    ok("paying an order twice just returns to the order page", http.post(f"/demo/pay/{order_id}", follow_redirects=False).status_code == 303)
    ok("a cross-site post to the console is refused",
       http.post("/demo/api/send", json={"text": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403)


if __name__ == "__main__":
    server = None
    if len(sys.argv) > 1:
        base = sys.argv[1].rstrip("/")
    else:
        server, base = start_server()
    try:
        story(base)
        print("\nAll steps passed.")
    finally:
        if server:
            server.terminate()
