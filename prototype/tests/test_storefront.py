"""The public store link: a catalog page for people and agents, a short buy form, a QRIS pay page and a return form.

The point under test: a person who clicks a link in an Instagram bio gets the same checkout, the same seller alerts and the
same return rules as an agent, and nothing a seller or a buyer typed can run as script.
"""

import itertools
import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app as real_app, ctx as real_ctx
from etalase.gateway import pages
from etalase.gateway.storefront import FIELD_LIMITS, parse_form
from etalase.models import CheckoutError, OrderStatus, now

_wa = itertools.count(6287800000100)
_phone = itertools.count(6285800000100)


@pytest.fixture(scope="module")
def web():
    return TestClient(real_app)    # no lifespan: it would start the MCP session manager a second time in this process


def shop(web, name="Toko Sari", text="Rattan bag, natural 5, price 150k"):
    s = web.post("/dev/sellers", json={"name": name, "whatsapp": str(next(_wa))}).json()
    drafts, _ = real_ctx.catalog.ingest(s["id"], [text], ["samples/voal-hijab.png"])
    real_ctx.catalog.publish(s["id"])
    return s, real_ctx.store.get_product(drafts[0].id)


def form(variant_id, phone=None, **over):
    data = {"qty": "1", "name": "Dewi Lestari", "phone": phone or f"0{next(_phone) - 6200000000}",
            "address": "Jl. Dago 10 No 5", "city": "Bandung", "postal": "40135", **over}
    return "&".join(f"{k}={v}" for k, v in data.items())


def post_form(web, path, body, **headers):
    return web.post(path, content=body, headers={"content-type": "application/x-www-form-urlencoded", **headers},
                    follow_redirects=False)


def order_id(response):
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[1]


# ---------------------------------------------------------------- the catalog
def test_the_store_page_lists_products_for_people_and_points_agents_at_the_machine_readable_data(web):
    s, p = shop(web)
    page = web.get(f"/stores/{s['id']}")
    assert page.status_code == 200 and "Toko Sari" in page.text and "Rp150.000" in page.text
    assert f"/stores/{s['id']}/p/{p.id}" in page.text            # a card opens the product page; checkout comes after it
    assert "application/ld+json" in page.text and "/mcp/" in page.text and "/.well-known/ucp" in page.text
    assert "<span>Products</span>" in page.text and "<span>Returns</span>" in page.text
    assert f"/stores/{s['id']}/returns" in page.text and "Returns within" in page.text
    assert 'content="index,follow"' in page.text
    assert s["whatsapp"] not in page.text                 # the seller's number is never published
    assert "default-src 'none'" in page.headers["content-security-policy"] and "form-action 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert web.get("/stores/sel_nope").status_code == 404


def scripts_of(html: str) -> list[str]:
    """The body of every script element that can run (a JSON-LD block is data, not code)."""
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*ld\+json)[^>]*>(.*?)</script>", html, re.S)]


def test_the_only_script_on_a_buyer_page_is_ours_and_the_csp_pins_exactly_that_script(web):
    s, p = shop(web)
    v = p.variants[0].id
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    pages_ = [web.get(f"/stores/{s['id']}"), web.get(f"/stores/{s['id']}/buy/{v}"), web.get(f"/pay/{oid}"),
              web.get(f"/stores/{s['id']}/returns")]
    for r in pages_:
        csp = r.headers["content-security-policy"]
        assert f"script-src {pages.SCRIPT_HASH};" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "font-src 'self'" in csp
        for body in scripts_of(r.text):
            assert body == pages.SCRIPT                      # nothing a seller or buyer typed ever becomes code
        assert not re.search(r"\son[a-z]+\s*=", r.text, re.I)  # and no inline event handlers (the policy would block them anyway)
        assert not re.search(r"<script[^>]+src=|<link[^>]+rel=\"?stylesheet|@import|url\(\s*[\"']?https?:", r.text)  # nothing is loaded from elsewhere
    # the hash in the header is the hash of the script the page carries, byte for byte
    import base64, hashlib
    body = scripts_of(pages_[2].text)[0]
    assert pages.SCRIPT_HASH == "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


def test_the_font_is_served_from_a_fixed_list_and_cached(web):
    r = web.get("/static/plus-jakarta-sans.woff2")
    assert r.status_code == 200 and r.headers["content-type"] == "font/woff2" and len(r.content) > 5_000
    assert "immutable" in r.headers["cache-control"] and r.content[:4] == b"wOF2"
    for bad in ("nope.woff2", "OFL-plus-jakarta-sans.txt", "instrument-serif.woff2", "..%2Fpages.py", ".env"):
        assert web.get(f"/static/{bad}").status_code == 404
    s, _ = shop(web)
    assert 'rel=preload href="/static/plus-jakarta-sans.woff2"' in web.get(f"/stores/{s['id']}").text


def test_a_product_without_a_photo_gets_a_quiet_placeholder_and_filler_text_is_not_shown(web):
    s, p = shop(web, text="Rattan bag, natural 5, price 150k")
    p = real_ctx.store.get_product(p.id)
    p.images = []
    p.description = f"{p.title}, available in Natural."          # what the offline extractor writes: it adds nothing
    real_ctx.store.put_product(p)
    v = p.variants[0].id
    store = re.sub(r"<script type=\"application/ld\+json\">.*?</script>", "", web.get(f"/stores/{s['id']}").text, flags=re.S)
    item = web.get(f"/stores/{s['id']}/p/{p.id}").text
    buy = web.get(f"/stores/{s['id']}/buy/{v}").text
    assert "class=ph" in store and "class=ph" in item and "class=ph" in buy and "<img" not in store.split("<main", 1)[1]
    assert "available in" not in store and "available in" not in item and "available in" not in buy
    assert "About this item" not in item                     # a description that only repeats the title is not shown
    p.description = "Hand-woven in Cirebon, lined with cotton, fits a 13 inch laptop."
    real_ctx.store.put_product(p)
    assert "Hand-woven in Cirebon" in web.get(f"/stores/{s['id']}/p/{p.id}").text


def test_submit_buttons_say_what_is_happening_and_the_order_page_polls_only_while_it_is_waiting(web):
    s, p = shop(web)
    v = p.variants[0].id
    assert "data-busy=" in web.get(f"/stores/{s['id']}/buy/{v}").text
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    assert re.search(r'<main[^>]* data-live="awaiting_seller_confirmation"', web.get(f"/pay/{oid}").text)
    real_ctx.checkout.seller_confirms_stock(s["id"], oid, available=True)
    waiting = web.get(f"/pay/{oid}").text
    assert re.search(r'<main[^>]* data-live="awaiting_payment:', waiting) and "data-left=" in waiting
    web.post(f"/dev/pay/{oid}")
    paid = web.get(f"/pay/{oid}").text
    assert "data-live" not in paid.split("<main", 1)[1].split(">", 1)[0] and "<noscript>" not in paid   # a finished state stops polling


def test_seller_text_cannot_run_as_script_or_break_out_of_the_json_ld(web):
    s, p = shop(web, name="<img src=x onerror=alert(1)> Shop")
    p.title = "Bag </script><script>alert(1)</script>"
    p.description = "<b>bold</b> & \"quotes\""
    real_ctx.store.put_product(p)
    page = web.get(f"/stores/{s['id']}").text
    buy = web.get(f"/stores/{s['id']}/buy/{p.variants[0].id}").text
    item = web.get(f"/stores/{s['id']}/p/{p.id}").text
    for html in (page, item, buy):
        assert "<script>alert" not in html and "<img src=x" not in html and "<b>bold</b>" not in html
        assert scripts_of(html) == [pages.SCRIPT]                       # still only our own script
    assert "&lt;img src=x" in page and "&lt;b&gt;bold&lt;/b&gt;" in item
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1)
    assert "</script" not in ld
    assert json.loads(ld)[0]["name"].startswith("Bag </script>")      # still the seller's words, only escaped for the page


def test_sold_out_items_have_no_buy_button_and_the_form_says_so(web):
    s, p = shop(web)
    v = p.variants[0]
    real_ctx.store.set_stock(s["id"], p.id, v.id, 0, "test")
    page = web.get(f"/stores/{s['id']}").text
    assert "Sold out" in page and f"/buy/{v.id}" not in page
    assert web.get(f"/stores/{s['id']}/buy/{v.id}").status_code == 410
    assert web.get(f"/stores/{s['id']}/buy/var_nope").status_code == 404
    other, _ = shop(web)
    assert web.get(f"/stores/{other['id']}/buy/{v.id}").status_code == 404      # another store's variant is not reachable here


# ---------------------------------------------------------------- the look and the pieces it is built from
def test_every_buyer_page_has_one_heading_a_skip_link_a_way_back_and_an_app_bar(web):
    s, p = shop(web)
    v = p.variants[0].id
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    for url in (f"/stores/{s['id']}", f"/stores/{s['id']}/p/{p.id}", f"/stores/{s['id']}/buy/{v}", f"/pay/{oid}",
                f"/stores/{s['id']}/returns", "/stores/nope"):
        html = web.get(url).text
        assert html.count("<h1") == 1 and "<html lang=en>" in html and 'class=skip href=#main' in html
        assert "<main id=main" in html and "<header id=bar" in html and 'name=viewport' in html and "viewport-fit=cover" in html
    assert 'aria-label="Back to Toko Sari"' in web.get(f"/stores/{s['id']}/p/{p.id}").text      # the app bar's way back
    assert 'aria-label="Back to Checkout"' in web.get(f"/stores/{s['id']}/buy/{v}").text


def test_a_product_card_opens_the_product_page_which_lets_the_buyer_switch_option_without_leaving_it(web):
    s, p = shop(web, text="Hijab voal, black 5, navy 3, mocca 10, price 85k")
    store = web.get(f"/stores/{s['id']}").text
    links = re.findall(r'<a class="pc rise"[^>]*href="(/stores/[^"]+/p/(prd_[a-z0-9]+))"', store)
    assert len(links) == 1 and links[0][1] == p.id           # one link per product, not one per colour
    page = web.get(links[0][0]).text
    assert page.count("aria-current=true") == 1 and 'class="chip on"' in page
    assert len(re.findall(r'<a class="chip', page)) == 3
    ids = [v.id for v in p.variants]
    assert all(f'data-buy="/stores/{s["id"]}/buy/{i}"' in page for i in ids)          # every colour carries its own checkout link
    assert all(f"?v={i}" in page for i in ids)                                         # and a plain link that works without JavaScript
    first = re.search(r'<a class=btn data-go href="([^"]+)">Buy now</a>', page).group(1)
    other = ids[-1]
    switched = web.get(f"/stores/{s['id']}/p/{p.id}?v={other}").text
    assert f'<a class=btn data-go href="/stores/{s["id"]}/buy/{other}">Buy now</a>' in switched and first != f"/stores/{s['id']}/buy/{other}"
    assert "Payment protected" in page and "data-share" in page and 'class=dock' in page
    assert web.get(f"/stores/{s['id']}/p/prd_nope").status_code == 404
    checkout = web.get(f"/stores/{s['id']}/buy/{ids[0]}").text
    assert "QRIS" in checkout and "Change option" in checkout and f"/p/{p.id}?v={ids[0]}" in checkout


def test_the_store_page_has_stats_search_and_sort_and_swatches_for_colour_options(web):
    s, p = shop(web, text="Hijab voal, black 5, navy 3, mocca 10, price 85k")
    s2, p2 = shop(web)
    store = web.get(f"/stores/{s['id']}").text
    assert 'class=sw' in store and store.count("<i style=\"background:") == 3            # three colour dots, from the option names
    assert "<span>Orders done</span>" not in store and "<span>Products</span>" in store   # a new store shows where it ships from, not a zero
    for sid, expected in ((s2["id"], "Search this store"),):
        assert expected not in web.get(f"/stores/{sid}").text                              # one product: nothing to search
    real_ctx.catalog.ingest(s["id"], ["Rattan bag, natural 5, price 150k"], ["samples/voal-hijab.png"])
    real_ctx.catalog.publish(s["id"])
    two = web.get(f"/stores/{s['id']}").text
    assert 'placeholder="Search this store"' in two and "data-sort=asc" in two and "data-sort=desc" in two
    assert 'data-price="85000"' in two and 'data-price="150000"' in two


def test_the_buy_page_has_a_quantity_stepper_and_a_subtotal_that_follows_it(web):
    s, p = shop(web)
    v = p.variants[0]
    page = web.get(f"/stores/{s['id']}/buy/{v.id}").text
    assert f'data-unit="{v.price}"' in page and "data-step=1" in page and "data-step=-1" in page and "data-subtotal" in page
    assert "Rp150.000" in page.split("data-subtotal>", 1)[1].split("<", 1)[0]
    bad = post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, qty="3", postal="1"))
    assert bad.status_code == 422 and "Rp450.000" in bad.text.split("data-subtotal>", 1)[1].split("<", 1)[0]   # kept after an error
    assert "Postal code must be 5 digits." in bad.text and 'aria-invalid=true' in bad.text and 'class="field bad"' in bad.text   # and the field is marked


def test_the_order_page_shows_a_four_step_tracker_a_countdown_and_the_qr_code(web):
    s, p = shop(web)
    v = p.variants[0].id
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    real_ctx.checkout.seller_confirms_stock(s["id"], oid, available=True)
    page = web.get(f"/pay/{oid}").text
    assert page.count("<li class=done>") == 1 and page.count("aria-current=step") == 1 and "Your details" in page and "Delivered" in page
    assert re.search(r'<span data-left="\d+">\d+:\d\d</span> left to pay', page) and "<svg" in page.split("class=qr", 1)[1]
    assert "Total to pay" in page and "Pay before" in page and "data-saveqr" in page and "Save QR image" in page
    assert "This code has expired" in page                # shown by the script when the countdown ends


def test_the_dashboard_shares_the_design_tokens_and_icons_and_loads_the_same_font(web):
    page = web.get("/").text
    assert "@@" not in page                                              # both placeholders were filled
    assert '<symbol id="i-copy"' in page and "--brand:" in page and "Plus Jakarta Sans" in page
    assert 'rel="preload" href="/static/plus-jakarta-sans.woff2"' in page and "instrument" not in page.lower()


def test_icons_the_status_card_and_the_summary_escape_what_they_are_given(web):
    assert pages.ico("check") == '<svg class="ico" aria-hidden="true"><use href="#i-check"/></svg>'
    assert all(f'id="i-{name}"' in pages.sprite() for name in pages.ICONS)
    html = pages.summary([("<b>x</b> × 1", "Rp1")], ("Total", "Rp1"))
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert pages.tone("a") == pages.tone("a") and pages.tone_style("sel_1").startswith("--tone:#")


# ---------------------------------------------------------------- buying
def test_a_person_buys_from_the_link_and_the_order_lands_in_the_sellers_whatsapp(web):
    s, p = shop(web)
    v = p.variants[0]
    assert "Delivery address" in web.get(f"/stores/{s['id']}/buy/{v.id}").text
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, qty="2")))
    order = real_ctx.store.get_order(oid)
    assert order.seller_id == s["id"] and order.items[0].qty == 2 and order.buyer.phone.startswith("62")
    page = web.get(f"/pay/{oid}").text
    if order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION:
        assert "confirming that the item is in stock" in page
        real_ctx.checkout.seller_confirms_stock(s["id"], oid, available=True)
        page = web.get(f"/pay/{oid}").text
    assert "<svg" in page and "Rp" in page and 'content="noindex,nofollow"' in page
    # the page keeps itself current: a script swaps the status in place, and without JavaScript a meta refresh does it
    assert "data-live=" in page and "<noscript><meta http-equiv=refresh" in page and "data-left=" in page
    assert str(order.buyer.phone) not in page and "Jl. Dago" not in page          # the page never repeats personal data
    real_ctx.checkout.payment_succeeded(real_ctx.store.get_order(oid).payment.reference)
    assert "Paid" in web.get(f"/pay/{oid}").text
    alerts = [m.text for m in real_ctx.messenger.outbox if m.to == s["whatsapp"]
              and ("paid order" in m.text.lower() or "payment received" in m.text.lower())]
    assert alerts, "the seller must be told about an order that came from the link"


def test_bad_input_shows_the_error_with_the_form_filled_in_and_creates_nothing(web):
    s, p = shop(web)
    v = p.variants[0]
    before = len(real_ctx.store.list_orders(s["id"]))
    r = post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, postal="1", name="<b>Eve</b>"))
    assert r.status_code == 422 and "postal code" in r.text.lower()
    assert "&lt;b&gt;Eve&lt;/b&gt;" in r.text and "<b>Eve</b>" not in r.text         # typed values come back escaped
    assert post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, qty="abc")).status_code == 422
    assert post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, qty="99")).status_code == 422
    assert post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, phone="12")).status_code == 422
    assert post_form(web, f"/stores/{s['id']}/buy/{v.id}", "x=" + "a" * 9000).status_code == 413
    assert len(real_ctx.store.list_orders(s["id"])) == before


def test_a_form_post_from_another_site_is_refused(web):
    s, p = shop(web)
    v = p.variants[0]
    r = post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id), origin="https://evil.example")
    assert r.status_code == 403 and real_ctx.store.list_orders(s["id"]) == []
    ok = post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id), origin="http://testserver")
    assert ok.status_code == 303


def test_buyer_pages_use_a_same_origin_referrer_policy_so_real_browsers_send_their_origin(web):
    """Found in a real browser: under the API-wide `no-referrer` Chrome sends `Origin: null` on the form post, and the
    cross-site check then refused every real purchase. These pages must send `same-origin`, and `null` stays refused."""
    s, p = shop(web)
    v = p.variants[0]
    for url in (f"/stores/{s['id']}", f"/stores/{s['id']}/buy/{v.id}", "/pay/ord_nope"):
        assert web.get(url).headers["referrer-policy"] == "same-origin", url
    assert post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id), origin="null").status_code == 403
    assert web.get("/healthz").headers["referrer-policy"] == "no-referrer"       # the rest of the API stays strict


def test_an_unverified_seller_cannot_take_orders_through_the_link(web):
    s, p = shop(web)
    seller = real_ctx.store.get_seller(s["id"])
    seller.kyc_status = "pending"
    real_ctx.store.put_seller(seller)
    v = p.variants[0]
    r = post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id))
    assert r.status_code == 422 and "not accepting orders" in r.text.lower()


def test_the_pay_page_for_an_unknown_order_is_a_404_and_never_cached(web):
    r = web.get("/pay/ord_nope")
    assert r.status_code == 404 and r.headers["cache-control"] == "no-store"
    s, p = shop(web)
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{p.variants[0].id}", form(p.variants[0].id)))
    assert web.get(f"/pay/{oid}").headers["content-security-policy"].startswith("default-src 'none'")


# ---------------------------------------------------------------- after delivery
def deliver(web, s, oid, waybill):
    real_ctx.checkout.seller_confirms_stock(s["id"], oid, available=True) \
        if real_ctx.store.get_order(oid).status == OrderStatus.AWAITING_SELLER_CONFIRMATION else None
    real_ctx.checkout.payment_succeeded(real_ctx.store.get_order(oid).payment.reference)
    real_ctx.checkout.seller_accepts(s["id"], oid)
    real_ctx.checkout.mark_shipped(s["id"], oid, waybill)
    assert "tracking number" in web.get(f"/pay/{oid}").text and waybill in web.get(f"/pay/{oid}").text
    real_ctx.checkout.shipment_update(waybill, "delivered")


def test_a_person_can_ask_for_a_return_from_the_order_page(web):
    s, p = shop(web)
    v = p.variants[0]
    phone = "0" + str(next(_phone) - 6200000000)
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id, phone=phone)))
    deliver(web, s, oid, "JNE717171")
    page = web.get(f"/pay/{oid}").text
    assert "Delivered" in page and f"/pay/{oid}/return" in page
    assert "Why do you want to return it?" in web.get(f"/pay/{oid}/return").text
    assert "changed my mind" not in web.get(f"/pay/{oid}/return").text           # the seller did not opt in

    wrong = post_form(web, f"/pay/{oid}/return", "reason=damaged&phone=0811111111&note=torn")
    assert wrong.status_code == 422 and "checkout not found" in wrong.text.lower()      # a guessed order id proves nothing
    mind = post_form(web, f"/pay/{oid}/return", f"reason=changed_mind&phone={phone}")
    assert mind.status_code == 422 and "does not accept returns for a change of mind" in mind.text.lower()

    ok = post_form(web, f"/pay/{oid}/return", f"reason=damaged&phone={phone}&note=%3Cb%3Eseam%3C%2Fb%3E+torn")
    assert ok.status_code == 303 and real_ctx.store.get_order(oid).status == OrderStatus.RETURN_REQUESTED
    assert "with the seller" in web.get(f"/pay/{oid}").text
    request = next(m.text for m in real_ctx.messenger.outbox if m.to == s["whatsapp"] and "Return request" in m.text)
    assert "seam" in request                                                        # the seller sees what the buyer wrote
    assert post_form(web, f"/pay/{oid}/return", f"reason=damaged&phone={phone}").status_code == 422     # only one return per order

    real_ctx.returns.seller_responds(s["id"], oid, approve=True)
    assert "Return approved" in web.get(f"/pay/{oid}").text
    real_ctx.returns.seller_received(s["id"], oid)
    assert "Refunded" in web.get(f"/pay/{oid}").text or "refund is being sent" in web.get(f"/pay/{oid}").text


def test_the_return_form_offers_change_of_mind_only_when_the_order_was_made_under_those_terms(web):
    s, p = shop(web)
    real_ctx.returns.set_policy(s["id"], True)
    v = p.variants[0]
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v.id}", form(v.id)))
    assert "changed my mind" in web.get(f"/pay/{oid}/return").text
    real_ctx.returns.set_policy(s["id"], False)
    assert "changed my mind" in web.get(f"/pay/{oid}/return").text              # the terms of the purchase hold


def test_return_posts_from_another_site_are_refused(web):
    s, p = shop(web)
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{p.variants[0].id}", form(p.variants[0].id)))
    r = post_form(web, f"/pay/{oid}/return", "reason=damaged&phone=0811111111", origin="https://evil.example")
    assert r.status_code == 403


# ---------------------------------------------------------------- the seller's side of the link
def test_the_seller_gets_the_link_when_publishing_and_can_ask_for_it(web):
    s = web.post("/dev/sellers", json={"name": "Link Shop", "whatsapp": str(next(_wa))}).json()
    real_ctx.catalog.ingest(s["id"], ["Rattan bag, natural 5, price 150k"], ["samples/voal-hijab.png"])
    real_ctx.inbox.handle_button(s["id"], "publish")
    url = f"{real_ctx.settings.public_base_url.rstrip('/')}/stores/{s['id']}"
    assert any(url in m.text for m in real_ctx.messenger.outbox if m.to == s["whatsapp"])
    for text in ("shop link", "link toko", "my store link"):
        real_ctx.messenger.outbox.clear()
        real_ctx.inbox.handle_intent(s["id"], text)
        assert url in real_ctx.messenger.outbox[-1].text, text


def test_the_dashboard_api_includes_the_store_url(web):
    s = web.post("/dev/sellers", json={"name": "Api Link Shop", "whatsapp": str(next(_wa))}).json()
    web.post("/auth/otp", json={"whatsapp": s["whatsapp"]})
    code = re.search(r"\b(\d{6})\b", [m for m in web.get("/dev/outbox", params={"to": s["whatsapp"]}).json()
                                     if "code" in m["text"].lower()][-1]["text"]).group(1)
    token = web.post("/auth/verify", json={"whatsapp": s["whatsapp"], "code": code}).json()["token"]
    me = web.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["store_url"].endswith(f"/stores/{s['id']}")


# ---------------------------------------------------------------- helpers
def test_forms_are_parsed_clipped_and_size_limited():
    assert parse_form(b"name=" + b"a" * 500 + b"&city=Bandung")["name"] == "a" * FIELD_LIMITS["name"]
    assert parse_form(b"city=Bandung&city=Jakarta")["city"] == "Bandung"
    assert parse_form(b"") == {}
    with pytest.raises(CheckoutError):
        parse_form(b"x=" + b"a" * 9000)
