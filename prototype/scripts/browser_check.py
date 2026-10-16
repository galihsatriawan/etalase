"""Real-browser checks of what unit tests cannot see: that the buyer pages feel smooth and still work without JavaScript.

It boots the app on a free port with the mock adapters, drives it with headless Chromium and prints PASS or FAIL per check:
no Content-Security-Policy violations, links and forms swap the page in place (the document is never reloaded), the back button
restores the previous page, the option switching on the product page, search and sort, the quantity stepper and subtotal, the busy state and a single POST for a double tap, the live
order status the countdown and the expired code, saving the QR image, reduced motion, and the no-JavaScript fallback.

Optional: it needs Playwright and a browser (`pip install playwright && playwright install chromium`), which the rest of the
project does not. Run from the prototype folder:  python scripts/browser_check.py
"""
import os, re, socket, subprocess, sys, time, json
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "ETALASE_ENV": "dev", "PUBLIC_BASE_URL": base}
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "etalase.api.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
res = []
def ok(name, cond, extra=""):
    res.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
def poll(pg, fn, tries=80, ms=100):
    for _ in range(tries):
        v = fn()
        if v: return v
        pg.wait_for_timeout(ms)
    return None
try:
    for _ in range(60):
        try:
            if httpx.get(base + "/healthz", timeout=1).status_code == 200: break
        except httpx.HTTPError: time.sleep(.5)
    h = httpx.Client(base_url=base, timeout=20); WA = "6281234567898"
    sel = h.post("/dev/sellers", json={"name": "Toko Sari Hijab", "whatsapp": WA, "city": "Bandung", "postal_code": "40135", "address": "Jl. Dago 10"}).json()
    h.post("/dev/instagram/import", params={"seller_id": sel["id"]}); h.post("/dev/whatsapp", json={"sender": WA, "button_id": "publish"})
    store = f"/stores/{sel['id']}"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 700})
        errors, reqs = [], []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: reqs.append((r.method, r.url)))
        pg.add_init_script("document.addEventListener('securitypolicyviolation', e => (window.__v = window.__v || []).push(e.violatedDirective + ' ' + e.blockedURI))")
        pg.goto(base + store); pg.wait_for_timeout(600)
        ok("store: no CSP violation", not pg.evaluate("window.__v || []"), str(pg.evaluate("window.__v || []")))
        ok("store: Plus Jakarta Sans loaded", pg.evaluate("document.fonts.check('700 1rem \"Plus Jakarta Sans\"')"))
        ok("store: every photo faded in", pg.evaluate("[...document.querySelectorAll('.media img')].every(i => i.classList.contains('in'))"))
        ok("store: html has the js class", pg.evaluate("document.documentElement.classList.contains('js')"))
        ok("store: the name sits below the cover, not on it", pg.evaluate("document.querySelector('.shop h1').getBoundingClientRect().top >= document.querySelector('.cover').getBoundingClientRect().bottom - 1"))
        # search and sort
        total = pg.locator(".pc").count()
        pg.fill("#q", "pashmina"); pg.wait_for_timeout(100)
        visible = pg.locator(".pc:visible").count()
        ok("search: filters the cards as you type", 0 < visible < total, f"{visible}/{total}")
        pg.fill("#q", "zzzz"); pg.wait_for_timeout(100)
        ok("search: says so when nothing matches", pg.locator("#none").is_visible() and pg.locator(".pc:visible").count() == 0)
        pg.fill("#q", ""); pg.wait_for_timeout(100)
        prices = lambda: pg.evaluate("[...document.querySelectorAll('.grid .pc')].map(c => +c.dataset.price)")
        pg.click("[data-sort=desc]"); pg.wait_for_timeout(700)
        ok("sort: high to low reorders the cards (and the DOM)", prices() == sorted(prices(), reverse=True), str(prices()))
        pg.click("[data-sort=asc]"); pg.wait_for_timeout(700)
        ok("sort: low to high", prices() == sorted(prices()), str(prices()))
        pg.click("[data-sort=new]"); pg.wait_for_timeout(700)
        ok("sort: pressed state follows the choice", pg.get_attribute("[data-sort=new]", "aria-pressed") == "true" and pg.get_attribute("[data-sort=asc]", "aria-pressed") == "false")
        pg.evaluate("window.__marker = 'same document'")
        # hover prefetch
        card = pg.locator("a.pc").first
        card.hover(); pg.wait_for_timeout(500)
        pg.mouse.wheel(0, 120); pg.wait_for_timeout(300)
        # card -> product page
        card.click()
        poll(pg, lambda: "/p/" in pg.url)
        ok("card click: product page, URL changed", "/p/" in pg.url, pg.url)
        ok("card click: same document (no reload)", pg.evaluate("window.__marker") == "same document")
        ok("card click: title updated", "Toko Sari Hijab" in pg.title() and pg.title() != "Toko Sari Hijab", pg.title())
        ok("card click: scroll at top", pg.evaluate("scrollY") == 0)
        ok("product: name and big price shown", pg.locator("h1").inner_text().strip() != "" and pg.locator("[data-price-text]").inner_text().startswith("Rp"))
        ok("product: the photo is a 1:1-ish hero that leaves room for the price above the fold", pg.evaluate("document.querySelector('[data-price-text]').getBoundingClientRect().bottom < innerHeight"))
        first_url = pg.url
        first_price = pg.inner_text("[data-price-text]")
        first_buy = pg.get_attribute("a[data-go]", "href")
        # options switch in place
        chips = pg.locator("a.chip")
        if chips.count() > 1:
            chip = chips.nth(1)
            label = chip.inner_text().strip().split("\n")[0]
            chip.click(); pg.wait_for_timeout(200)
            ok("option: switched without leaving the page", pg.evaluate("window.__marker") == "same document" and pg.url != first_url and "?v=" in pg.url, pg.url)
            ok("option: the Buy now link follows the option", pg.get_attribute("a[data-go]", "href") != first_buy)
            ok("option: the chosen one is marked", pg.locator(".chip.on").count() == 1 and label in pg.locator(".chip.on").inner_text())
            ok("option: the heading names the option", label in pg.inner_text(".opt .cap"))
            chips.nth(0).click(); pg.wait_for_timeout(200)
            ok("option: back to the first one", pg.get_attribute("a[data-go]", "href") == first_buy and pg.inner_text("[data-price-text]") == first_price)
        # back / forward
        pg.go_back(); poll(pg, lambda: pg.url.endswith(store) and pg.locator(".grid").count() == 1)
        ok("back: store page again", pg.url.endswith(store) and pg.locator(".grid").count() == 1 and pg.evaluate("window.__marker") == "same document", f"{pg.url} grid={pg.locator('.grid').count()}")
        pg.go_forward(); poll(pg, lambda: "/p/" in pg.url and pg.locator("a[data-go]").count() == 1)
        ok("forward: product page again", "/p/" in pg.url and pg.evaluate("window.__marker") == "same document")
        # gallery dock + Buy now -> checkout in place
        ok("product: the Buy now bar is stuck to the bottom of the screen", pg.evaluate("(() => { const r = document.querySelector('.dock').getBoundingClientRect(); return Math.abs(r.bottom - innerHeight) < 2 })()"))
        pg.click("a[data-go]"); poll(pg, lambda: "/buy/" in pg.url and pg.locator("form").count() == 1)
        ok("buy now: checkout opened in place", "/buy/" in pg.url and pg.evaluate("window.__marker") == "same document", pg.url)
        # stepper and subtotal
        unit = int(pg.evaluate("document.querySelector('form').dataset.unit"))
        pg.locator("button[data-step='1']").click()
        sub = pg.locator("[data-subtotal]").first.inner_text()
        ok("stepper: quantity 2", pg.input_value("#qty") == "2")
        ok("stepper: both subtotals doubled", all(t.replace(".", "").replace("Rp", "") == str(unit * 2) for t in pg.locator("[data-subtotal]").all_inner_texts()), sub)
        pg.locator("button[data-step='-1']").click(); pg.locator("button[data-step='-1']").click()
        ok("stepper: never below 1", pg.input_value("#qty") == "1")
        # validation error in place, next to the field
        pg.fill("#name", "Dewi"); pg.fill("#phone", "0857001122"); pg.fill("#address", "Jl. Merdeka 5 No 3"); pg.fill("#city", "Bandung")
        pg.evaluate("document.querySelector('#postal').removeAttribute('pattern')"); pg.fill("#postal", "40")
        url_before = pg.url
        pg.click("button[type=submit]"); poll(pg, lambda: pg.locator(".alert").count() > 0)
        pg.wait_for_timeout(400)
        ok("bad postal: error shown in place", pg.locator(".alert").count() == 1 and pg.url == url_before and pg.evaluate("window.__marker") == "same document")
        ok("bad postal: says what to fix", "5 digits" in pg.inner_text(".alert"))
        ok("bad postal: the form kept what was typed", pg.input_value("#name") == "Dewi")
        ok("bad postal: the field is marked and focused", pg.get_attribute("#postal", "aria-invalid") == "true" and pg.evaluate("document.activeElement.id") == "postal")
        ok("bad postal: the field is on screen", pg.evaluate("(() => { const r = document.querySelector('#postal').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight })()"))
        # slow POST: progress + busy
        pg.fill("#postal", "40135")
        pg.evaluate("(() => { const f = window.fetch; window.fetch = (u, o) => ((o && o.method === 'POST') ? new Promise(r => setTimeout(r, 1800)) : Promise.resolve()).then(() => f(u, o)); })()")
        del reqs[:]
        pg.click("button[type=submit]")
        pg.wait_for_timeout(300)
        state = pg.evaluate("(() => { const b = document.querySelector('button[type=submit]'); return [b.getAttribute('aria-busy'), b.textContent] })()")
        ok("submit: busy label while waiting", state[0] == "true" and "Placing" in state[1], str(state))
        pg.evaluate("document.querySelector('form') && document.querySelector('form').requestSubmit && document.querySelector('form').requestSubmit()")
        pg.wait_for_timeout(250)
        ok("submit: progress line visible on a slow server", pg.evaluate("document.getElementById('prog').className").startswith("prog on"), pg.evaluate("document.getElementById('prog').className"))
        poll(pg, lambda: "/pay/ord_" in pg.url, tries=100)
        ok("order: reached the order page in place", "/pay/ord_" in pg.url and pg.evaluate("window.__marker") == "same document", pg.url)
        posts = [r for r in reqs if r[0] == "POST"]
        ok("submit: exactly one POST (double tap guard)", len(posts) == 1, str(posts))
        oid = pg.url.rsplit("/", 1)[1]
        poll(pg, lambda: pg.locator("[data-live]").count() > 0)
        txt = pg.inner_text("main")
        if "left to pay" in txt:
            ok("pay: the QR is on screen without scrolling on a small phone", pg.evaluate("document.querySelector('.qr').getBoundingClientRect().bottom <= innerHeight"))
            t1 = pg.inner_text("[data-left]"); pg.wait_for_timeout(2100); t2 = pg.inner_text("[data-left]")
            ok("countdown ticks", t1 != t2, f"{t1} -> {t2}")
            ok("QR is drawn", pg.locator(".qr svg").count() == 1)
            with pg.expect_download(timeout=8000) as dl:
                pg.click("[data-saveqr]")
            ok("save QR: downloads a PNG", dl.value.suggested_filename.endswith(".png"), dl.value.suggested_filename)
            pg.evaluate("document.querySelector('[data-left]').dataset.left = 0"); pg.wait_for_timeout(1300)
            ok("expired: the code is dimmed and says so", pg.evaluate("document.querySelector('.pay').classList.contains('dead')") and pg.locator(".gone").is_visible())
            h.post(f"/dev/pay/{oid}")
            ok("status swapped to Paid in place", poll(pg, lambda: "Paid" in pg.inner_text("main"), tries=100) is not None and pg.evaluate("window.__marker") == "same document")
            ok("polling stopped once paid", pg.evaluate("document.getElementById('main').hasAttribute('data-live')") is False)
            ok("track shows 3rd step current", pg.locator(".track li.done").count() == 2)
            # back restores what the buyer typed
            pg.go_back(); poll(pg, lambda: "/buy/" in pg.url and pg.locator("#name").count() == 1)
            ok("back from the order page: the checkout form still holds what was typed", pg.input_value("#name") == "Dewi" and pg.input_value("#city") == "Bandung", pg.input_value("#name"))
        else:
            ok("waiting for seller state (acceptable)", "Waiting" in txt, txt[:80])
        ok("no CSP violations on any page", not pg.evaluate("window.__v || []"), str(pg.evaluate("window.__v || []")))
        errors = [e for e in errors if "422" not in e]
        ok("no console/page errors (the one 422 is the deliberate bad postal code)", not errors, str(errors)[:300])
        # reduced motion
        c2 = b.new_context(reduced_motion="reduce", viewport={"width": 390, "height": 700}); p2 = c2.new_page(); p2.goto(base + store); p2.wait_for_timeout(200)
        ok("reduced motion: cards visible at once", p2.evaluate("getComputedStyle(document.querySelector('.pc')).opacity") == "1")
        # no JavaScript at all
        c3 = b.new_context(java_script_enabled=False, viewport={"width": 390, "height": 700}); p3 = c3.new_page(); p3.goto(base + store)
        p3.wait_for_timeout(1500); p3.locator("a.pc").first.click(force=True); p3.wait_for_load_state()
        ok("no-JS: card link is a normal navigation to the product page", "/p/" in p3.url, p3.url)
        ok("no-JS: the options and Buy now are plain links", p3.locator("a[data-go]").count() == 1)
        p3.wait_for_timeout(900)   # a cross-document view transition is still running right after a load
        p3.locator("a[data-go]").click(force=True); p3.wait_for_url(re.compile(r"/buy/"), timeout=10000)
        ok("no-JS: Buy now opens the checkout", "/buy/" in p3.url, p3.url)
        p3.wait_for_timeout(600); p3.fill("#name", "Rani"); p3.fill("#phone", "0857001133"); p3.fill("#address", "Jl. Braga 9"); p3.fill("#city", "Bandung"); p3.fill("#postal", "40111")
        p3.click("button[type=submit]"); p3.wait_for_url(re.compile(r"/pay/ord_"), timeout=15000)
        ok("no-JS: form posts and lands on the order page", "/pay/ord_" in p3.url)
        b.close()
finally:
    proc.terminate()
print("ALL OK" if all(res) else "SOME FAILED")
