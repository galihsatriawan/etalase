#!/usr/bin/env python3
"""An AI shopping surface (simulated) buys from Etalase over UCP, the Universal Commerce Protocol.

    python scripts/ucp_shopper.py                 # starts its own sandbox on a free port and pays with the demo button
    python scripts/ucp_shopper.py URL             # a server that is already running, for example the deployed sandbox
    python scripts/ucp_shopper.py URL --demo-pay  # ...and press the sandbox's simulated QRIS button at the end
    python scripts/ucp_shopper.py URL --wait 120  # ...or wait up to 120 seconds for a real payment of the QRIS code

This is a UCP *client written for this repository*, playing the part of an AI surface. It is not Google AI Mode and not the Gemini
app: no Google surface calls Etalase today (UCP checkout in Merchant Center is a US-only rollout by invitation). Every call it makes
is a real call to the server; only the surface is simulated. What it shows is the journey the UCP specification describes:

    discover the business -> open a session with two products -> send the buyer and the address -> choose a courier
    -> complete -> the server answers `requires_escalation` with a `continue_url` -> the buyer pays the QRIS code there
    -> the session becomes `completed` with an order.

Products are found with the agent search (`/agent/v1/search`), which stands in for the product feed and index a real surface uses.
Exit code 1 if a step does not give the answer the protocol promises.
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
UCP_HEADERS = {"UCP-Agent": 'profile="https://platform.example/profile"'}   # Etalase does not fetch this profile (SSRF), see docs/16-ucp.md
SESSIONS = "/ucp/v1/checkout-sessions"
BUYER = {"first_name": "Dewi", "last_name": "Lestari", "phone_number": "6285700112233"}
DESTINATION = {"street_address": "Jl. Dago 10", "address_locality": "Bandung", "address_region": "Jawa Barat",
               "postal_code": "40135", "address_country": "ID"}
BANNER = ("An AI shopping surface (simulated) buys from Etalase over UCP.\n"
          "This client is written for this repository. It is not Google AI Mode: no Google surface calls Etalase today.")


class Failed(Exception):
    """A step did not give the answer the protocol promises."""


def idr(minor: int) -> str:
    """UCP amounts are minor units (IDR has two decimals), so 8500000 is Rp 85.000."""
    return "Rp " + f"{minor // 100:,}".replace(",", ".")


def rupiah(whole: int) -> str:
    return "Rp " + f"{whole:,}".replace(",", ".")


def expect(cond, message):
    if not cond:
        raise Failed(message)


def find_items(http, queries, want=2):
    """Products to buy: the first in-stock variant of the first product each query finds, all from one shop (a UCP session is one shop's cart).
    A variant that needs the seller's confirmation is skipped: the session would wait for a person instead of showing the protocol."""
    picked, shop = [], None
    for q in queries:
        found = http.get("/agent/v1/search", params={"q": q})
        expect(found.status_code == 200, f"search for {q!r} answered {found.status_code}")
        for product in found.json()["results"]:
            if shop not in (None, product["seller"]["id"]) or any(p["product_id"] == product["product_id"] for p in picked):
                continue
            variant = next((v for v in product["variants"] if v["availability"] == "in_stock"), None)
            if variant:
                shop = product["seller"]["id"]
                picked.append({"product_id": product["product_id"], "variant_id": variant["variant_id"], "shop": product["seller"]["name"],
                               "label": product["title"] + " (" + "/".join(variant["options"].values()) + ")", "price": variant["price"]})
                break
        if len(picked) == want:
            break
    expect(len(picked) == want, f"found {len(picked)} in-stock products from one shop for {queries}, needed {want}")
    return picked


def shop(http, queries=("hijab", "shirt"), buyer=None, say=print, pay=None, wait=0):
    """The whole journey. Returns what happened, for a test or a video to check; `say` prints each step.
    `pay(http, payment_page_html)` pays the QRIS code (the sandbox's simulated button); `wait` polls for a payment made another way."""
    buyer = buyer or BUYER
    steps = []

    def step(number, title, text):
        steps.append((number, title, text))
        say(f"{number}  {title:<18}{text}")

    profile = http.get("/.well-known/ucp")
    expect(profile.status_code == 200, f"/.well-known/ucp answered {profile.status_code}")
    ucp = profile.json()["ucp"]
    capabilities = sorted(ucp["capabilities"]) if isinstance(ucp["capabilities"], dict) else sorted(c["name"] for c in ucp["capabilities"])
    step(1, "Discover", f"GET /.well-known/ucp: UCP {ucp['version']}, {', '.join(capabilities)}")

    items = find_items(http, list(queries))
    step(2, "Find products", " + ".join(f"{i['label']} {rupiah(i['price'])}" for i in items) + f"   [{items[0]['shop']}]")

    created = http.post(SESSIONS, headers=UCP_HEADERS, json={"line_items": [{"item": {"id": i["variant_id"]}, "quantity": 1} for i in items]})
    expect(created.status_code in (200, 201), f"create answered {created.status_code}: {created.text[:160]}")
    session = created.json()
    expect(len(session["line_items"]) == 2, "the session should hold both products")
    total = next(t["amount"] for t in session["totals"] if t["type"] == "total")
    step(3, "Open a session", f"POST {SESSIONS}: {session['status']}, total {idr(total)} for {len(session['line_items'])} products")

    lines = [{"id": line["id"], "item": {"id": line["item"]["id"]}, "quantity": line["quantity"]} for line in session["line_items"]]
    updated = http.put(f"{SESSIONS}/{session['id']}", headers=UCP_HEADERS, json={
        "line_items": lines, "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DESTINATION]}]}})
    expect(updated.status_code == 200, f"update answered {updated.status_code}: {updated.text[:160]}")
    session = updated.json()
    options = session["fulfillment"]["methods"][0]["groups"][0]["options"]
    expect(options, "no courier option came back")
    quotes = ", ".join(f"{o['title']} {idr(o['totals'][0]['amount'])}" for o in options if o.get("totals"))
    step(4, "Buyer + address", f"PUT: {session['status']}, couriers: {quotes}")

    chosen = http.put(f"{SESSIONS}/{session['id']}", headers=UCP_HEADERS, json={"buyer": buyer, "fulfillment": {"methods": [
        {"type": "shipping", "destinations": [DESTINATION], "groups": [{"selected_option_id": options[0]["id"]}]}]}})
    expect(chosen.status_code == 200, f"choosing a courier answered {chosen.status_code}")
    session = chosen.json()
    expect(session["status"] == "ready_for_complete", f"expected ready_for_complete, got {session['status']}")
    total = next(t["amount"] for t in session["totals"] if t["type"] == "total")
    step(5, "Choose a courier", f"PUT: {options[0]['title']}, {session['status']}, total {idr(total)}")

    # No card or AP2 instrument: QRIS cannot be paid inside a chat, so the buyer is handed over to the page that shows the code.
    done = http.post(f"{SESSIONS}/{session['id']}/complete", headers=UCP_HEADERS, json={"payment": {"instruments": []}})
    expect(done.status_code == 200, f"complete answered {done.status_code}: {done.text[:160]}")
    done = done.json()
    expect(done["status"] == "requires_escalation" and done.get("continue_url"), f"expected requires_escalation with a continue_url, got {done['status']}")
    step(6, "Complete", f"POST .../complete: {done['status']} ({', '.join(m.get('code', '') for m in done.get('messages', []))})")
    say(f"{'':<21}continue_url: {done['continue_url']}")

    # The hosted page is fetched through the same client: a shop's own address (toko.example.com) may not resolve from where this runs.
    page = http.get(urlparse(done["continue_url"]).path)
    expect(page.status_code == 200 and "QRIS" in page.text, f"the payment page answered {page.status_code}")
    result = {"steps": steps, "items": items, "session_id": session["id"], "continue_url": done["continue_url"],
              "status": done["status"], "total": total, "couriers": [o["title"] for o in options]}

    deadline = time.monotonic() + wait
    if pay:
        pay(http, page.text)
    while True:
        status = http.get(f"{SESSIONS}/{session['id']}", headers=UCP_HEADERS).json()
        if status["status"] == "completed" or time.monotonic() >= deadline:
            break
        time.sleep(2)
    result["status"] = status["status"]
    if status["status"] == "completed":
        result["order"] = status["order"]["id"]
        step(7, "Buyer pays", f"the QRIS page, then GET {SESSIONS}/...: completed, order {result['order']}")
    elif pay or wait:
        raise Failed(f"the session is {status['status']}, not completed, after the payment")
    else:
        say(f"{'':<21}Stopped here: the buyer pays the QRIS code on that page, and the session then becomes `completed`.")
    return result


def demo_button(http, payment_page):
    """The sandbox's simulated QRIS payment: the same button the order page shows. Only a DEMO_MODE server has it, and only its page links to the order."""
    order = re.search(r"/pay/(ord_[0-9a-f]+)", payment_page)
    expect(order, "the payment page has no demo button: this is not a sandbox (DEMO_MODE), so pay the QRIS code and use --wait")
    r = http.post(f"/demo/pay/{order.group(1)}")
    expect(r.status_code in (200, 303), f"the demo payment button answered {r.status_code}: is this a sandbox (DEMO_MODE)?")


def start_sandbox():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "ETALASE_ENV": "dev", "PUBLIC_BASE_URL": base, "DEMO_MODE": "true"}
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="An AI shopping surface (simulated) buys from Etalase over UCP.")
    ap.add_argument("url", nargs="?", help="a running Etalase server; without it a sandbox is started on a free port")
    ap.add_argument("--query", action="append", help="what to search for, twice (default: hijab, shirt)")
    ap.add_argument("--demo-pay", action="store_true", help="press the sandbox's simulated QRIS button at the end (DEMO_MODE servers only)")
    ap.add_argument("--wait", type=int, default=0, help="seconds to wait for the QRIS code to be paid by someone")
    args = ap.parse_args(argv)
    proc = None
    try:
        if args.url:
            base = args.url.rstrip("/")
        else:
            proc, base = start_sandbox()
        print(BANNER + "\n")
        pay = demo_button if (args.demo_pay or proc) else None
        with httpx.Client(base_url=base, timeout=20) as http:
            shop(http, tuple(args.query or ("hijab", "shirt")), pay=pay, wait=args.wait)
        return 0
    except Failed as e:
        print(f"FAILED: {e}")
        return 1
    except httpx.HTTPError as e:
        print(f"FAILED: could not talk to the server: {e}")
        return 1
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
