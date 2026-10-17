"""Real-browser check of the judge sandbox: what scripts/e2e_judge.py cannot see because it has no JavaScript.

It boots the app with DEMO_MODE on a free port, then plays the reviewer in headless Chromium: types into the simulated seller phone with a
suggestion chip and a sample photo, taps Publish in the chat, buys on the shop page, pays with the demo button (the page script posts that
form in place), sees the seller's chat update, runs the order from the console, and signs in to the dashboard with the demo shortcut.
Prints PASS or FAIL per check; exit code 1 if any failed.

The first version of the demo button was a form with no field. The page script (gateway/pages.py) posts a form in place only when its
body is not empty, so in a browser the button did nothing while every HTTP-level test passed. This script exists to catch that kind of thing.

Optional: it needs Playwright and a browser (`pip install playwright && playwright install chromium`).  Run from the prototype folder:
    python scripts/browser_judge.py            # screenshots with  --shots DIR
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--shots", type=Path, help="save screenshots in this folder")
args = ap.parse_args()
if args.shots:
    args.shots.mkdir(parents=True, exist_ok=True)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), "ETALASE_ENV": "dev", "PUBLIC_BASE_URL": base, "DEMO_MODE": "true"}
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "etalase.api.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
results = []


def ok(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def shot(page, name):
    if args.shots:
        page.screenshot(path=str(args.shots / f"{name}.png"), full_page=True)


def seen(page, selector, ms=8000):
    try:
        page.wait_for_selector(selector, timeout=ms)
        return True
    except Exception:
        return False


try:
    for _ in range(60):
        try:
            if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        phone = browser.new_page(viewport={"width": 1280, "height": 900})
        phone.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        phone.on("pageerror", lambda e: errors.append(str(e)))
        phone.goto(base + "/demo")
        ok("the seller phone loads with its links", seen(phone, "#links a"))

        phone.click("#chips button >> text=Catalog")          # fills the text and picks the matching sample photo
        ok("the suggestion also selects its sample photo", phone.locator(".thumb[aria-pressed=true]").count() == 1)
        phone.click("#send")
        ok("sending clears the photo choice", seen(phone, ".msg.me") and phone.locator(".thumb[aria-pressed=true]").count() == 0)
        ok("a suggestion plus a sample photo gets a draft with a Publish button", seen(phone, ".msg.etalase .btns button >> text=Publish"))
        ok("what the seller sent shows as a bubble with the photo", phone.locator(".msg.me img").count() == 1)
        phone.click(".msg.etalase .btns button >> text=Publish")
        ok("tapping Publish in the chat publishes", seen(phone, "text=can now be found"))
        shot(phone, "1-phone-after-publish")

        shop = browser.new_page(viewport={"width": 390, "height": 844})
        shop.on("pageerror", lambda e: errors.append(str(e)))
        shop.goto(f"{base}/stores/sel_demo")
        ok("the shop lists the seeded products and the new one", all(shop.locator(f"text={t}").count() for t in ("Voal Hijab", "Linen Shirt", "Plain Scarf")))
        shop.goto(f"{base}/stores/sel_demo/buy/var_demo_voal_black")
        shop.fill("#name", "Dewi")
        shop.fill("#phone", "0857 0011 2233")
        shop.fill("#address", "Jl. Dago 10")
        shop.fill("#city", "Bandung")
        shop.fill("#postal", "40135")
        shop.click("button[type=submit]")
        ok("the order page shows the QR code and the demo button", seen(shop, "text=Simulate the QRIS payment"))
        shot(shop, "2-order-page-before-payment")
        shop.click("text=Simulate the QRIS payment")
        ok("the demo button pays the order in place (no reload, no error toast)", seen(shop, 'text="Paid"') and not shop.locator(".toast.on").count())
        shot(shop, "3-order-page-paid")

        phone.bring_to_front()          # a background tab does not poll; coming back refreshes at once
        ok("the seller's chat shows 'New paid order' with Accept", seen(phone, "text=New paid order"))
        phone.click(".msg.etalase .btns button >> text=Accept")
        phone.fill("#text", "tracking JNE123456")
        phone.click("#send")
        ok("the console says what is next, then offers the courier step", seen(phone, ".order .next >> text=courier") and seen(phone, "button:has-text('Courier delivers')"))
        phone.click("button:has-text('Courier delivers')")
        ok("after delivery the console offers to skip the return window", seen(phone, "button:has-text('Skip the return window')"))
        phone.click("button:has-text('Skip the return window')")
        ok("the order ends paid out", seen(phone, ".order .pill >> text=paid out"))
        shot(phone, "4-phone-paid-out")

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(base + "/demo")
        seen(mobile, "#links a")
        ok("the seller phone has no sideways scroll on a phone", mobile.evaluate("document.documentElement.scrollWidth") <= 390)

        dash = browser.new_page(viewport={"width": 390, "height": 844})
        dash.goto(base + "/")
        dash.click("#demo-in")
        ok("the dashboard's demo shortcut signs in as the demo seller", seen(dash, "#app:not([hidden])") and dash.inner_text("#title") == "Toko Demo Rina")
        shot(dash, "5-dashboard")
        ok("no console or page errors", not errors, "; ".join(errors)[:200])
        browser.close()
finally:
    proc.terminate()

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
