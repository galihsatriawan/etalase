"""The cart on the buyer pages: one cart per shop in a cookie, add / change / remove with plain forms, and one order for the whole cart.

What is under test: the cookie holds only variant ids and quantities and is never trusted (titles, prices, stock and whether the shop
is open are looked up again on every request); a cart is one shop's; checkout is all or nothing; and a cross-site form post can
neither fill a cart nor place an order.
"""

import itertools
import re

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app as real_app, ctx as real_ctx, limits as real_limits
from etalase.gateway import storefront
from etalase.models import OrderStatus, ProductStatus
from etalase.seller.format import rupiah

from .conftest import make_product, make_seller, variant

_wa = itertools.count(6287900000100)
_phone = itertools.count(6285900000100)
_runs = itertools.count()

FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture
def web():
    """A fresh client (and so a fresh cookie jar) per test, like a new browser."""
    real_limits._hits.clear()     # the app's limiter is shared by the whole test run
    yield TestClient(real_app)
    real_limits._hits.clear()


def word(n):
    return "".join(chr(97 + int(d)) for d in str(n))


def open_shop(kyc="verified"):
    """A shop with two products, a hijab in three colours and a bag in one, each sold in words nobody else sells."""
    n = word(next(_runs))
    seller = make_seller(real_ctx, name=f"Toko Keranjang {n}", wa=str(next(_wa)), kyc=kyc)
    hijab = make_product(real_ctx, seller, f"Kerudung{n} voal, black 5, navy 3, mocca 10, price 85k")
    bag = make_product(real_ctx, seller, f"Tas{n} rotan, natural 5, price 150k")
    return real_ctx.store.get_seller(seller.id), hijab, bag


def cookie_name(seller):
    return storefront.cart_cookie(seller.id)


def add(web, seller, v, qty=1, **headers):
    return web.post(f"/stores/{seller.id}/cart/add", content=f"variant_id={v.id}&qty={qty}", headers={**FORM, **headers},
                    follow_redirects=False)


def change(web, seller, action, v, qty=None):
    body = f"variant_id={v.id}" + (f"&qty={qty}" if qty is not None else "")
    return web.post(f"/stores/{seller.id}/cart/{action}", content=body, headers=FORM, follow_redirects=False)


def order_form(**over):
    data = {"name": "Dewi Lestari", "phone": f"0{next(_phone) - 6200000000}", "address": "Jl. Dago 10 No 5", "city": "Bandung",
            "postal": "40135", **over}
    return "&".join(f"{k}={v}" for k, v in data.items())


def checkout(web, seller, body=None, **headers):
    return web.post(f"/stores/{seller.id}/cart/checkout", content=body or order_form(), headers={**FORM, **headers},
                    follow_redirects=False)


def raw_cart(seller, text):
    """A request that carries exactly this cookie, whatever the cart code would have written itself."""
    return {"cookie": f"{cookie_name(seller)}={text}"}


def badge(page):
    found = re.search(r"<span class=cartn aria-hidden=true>(\d+)</span>", page)
    return int(found.group(1)) if found else 0


# ---------------------------------------------------------------- filling the cart
def test_an_empty_cart_says_so_and_the_store_bar_has_a_cart_link_without_a_count(web):
    seller, _, _ = open_shop()
    page = web.get(f"/stores/{seller.id}/cart")
    assert page.status_code == 200 and "Your cart is empty" in page.text and "Browse the store" in page.text
    store = web.get(f"/stores/{seller.id}").text
    assert f'href="/stores/{seller.id}/cart"' in store and badge(store) == 0
    assert web.get(f"/stores/{seller.id}/cart/checkout", follow_redirects=False).headers["location"] == f"/stores/{seller.id}/cart"


def test_adding_redirects_to_the_cart_and_keeps_only_variant_ids_and_quantities_in_the_cookie(web):
    seller, hijab, _ = open_shop()
    black = variant(hijab, "Black")
    r = add(web, seller, black, 2)
    assert r.status_code == 303 and r.headers["location"] == f"/stores/{seller.id}/cart" and r.headers["cache-control"] == "no-store"
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"{cookie_name(seller)}={black.id}:2;") and "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie
    assert hijab.title not in set_cookie and "85000" not in set_cookie        # no title, no price: those are looked up each time
    page = web.get(f"/stores/{seller.id}/cart")
    assert hijab.title in page.text and "Rp85.000 × 2 = Rp170.000" in page.text
    assert badge(web.get(f"/stores/{seller.id}").text) == 2 and "Cart, 2 items" in web.get(f"/stores/{seller.id}").text


def test_adding_the_same_option_again_adds_to_its_line_and_another_option_makes_a_new_line(web):
    seller, hijab, bag = open_shop()
    black, navy, natural = variant(hijab, "Black"), variant(hijab, "Navy"), bag.variants[0]
    for v in (black, black, navy, natural):
        assert add(web, seller, v).status_code == 303
    page = web.get(f"/stores/{seller.id}/cart").text
    assert page.count("class=\"cartline\"") == 3
    assert "Rp85.000 × 2 = Rp170.000" in page and "Rp150.000 × 1 = Rp150.000" in page
    assert "Rp405.000" in page and badge(web.get(f"/stores/{seller.id}").text) == 4          # 2 x 85k + 85k + 150k


def test_update_sets_a_quantity_zero_removes_the_line_and_remove_removes_it(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    add(web, seller, black)
    add(web, seller, natural)
    assert change(web, seller, "update", black, 4).status_code == 303
    assert "Rp85.000 × 4 = Rp340.000" in web.get(f"/stores/{seller.id}/cart").text
    change(web, seller, "update", natural, 0)
    page = web.get(f"/stores/{seller.id}/cart").text
    assert bag.title not in page and "Rp340.000" in page
    change(web, seller, "remove", black)
    assert "Your cart is empty" in web.get(f"/stores/{seller.id}/cart").text
    assert cookie_name(seller) not in web.cookies.keys() or web.cookies.get(cookie_name(seller)) in (None, "")
    assert change(web, seller, "remove", black).status_code == 303                           # removing what is not there is harmless
    assert web.post(f"/stores/{seller.id}/cart/explode", content=f"variant_id={black.id}", headers=FORM).status_code == 404


def test_a_quantity_is_clamped_and_must_be_a_number(web):
    seller, hijab, _ = open_shop()
    black = variant(hijab, "Black")
    top = real_ctx.settings.max_qty_per_order
    add(web, seller, black, 99)
    assert f"× {top} =" in web.get(f"/stores/{seller.id}/cart").text
    add(web, seller, black, 99)                                                              # adding more never goes past the limit
    assert f"× {top} =" in web.get(f"/stores/{seller.id}/cart").text
    r = web.post(f"/stores/{seller.id}/cart/add", content=f"variant_id={black.id}&qty=lots", headers=FORM)
    assert r.status_code == 422 and "Quantity must be a number" in r.text


def test_a_variant_that_is_not_here_cannot_be_added(web):
    seller, hijab, _ = open_shop()
    other, other_hijab, _ = open_shop()
    assert add(web, seller, variant(other_hijab, "Black")).status_code == 404                # another shop's option
    assert web.post(f"/stores/{seller.id}/cart/add", content="variant_id=var_nope&qty=1", headers=FORM).status_code == 404
    assert web.post(f"/stores/{seller.id}/cart/add", content="qty=1", headers=FORM).status_code == 404
    real_ctx.store.set_stock(seller.id, hijab.id, variant(hijab, "Navy").id, 0, "test")
    sold_out = add(web, seller, variant(hijab, "Navy"))
    assert sold_out.status_code == 422 and "sold out" in sold_out.text
    assert cookie_name(seller) not in sold_out.headers.get("set-cookie", "")


def test_a_cart_is_limited_to_a_few_lines(web, monkeypatch):
    monkeypatch.setattr(storefront, "CART_MAX_LINES", 2)
    seller, hijab, bag = open_shop()
    add(web, seller, variant(hijab, "Black"))
    add(web, seller, variant(hijab, "Navy"))
    full = add(web, seller, bag.variants[0])
    assert full.status_code == 422 and "cart is full" in full.text
    assert add(web, seller, variant(hijab, "Black")).status_code == 303                      # more of a line it already has is fine


# ---------------------------------------------------------------- a cookie nobody should trust
def test_a_tampered_cookie_only_ever_asks_for_things_a_buyer_could_ask_for(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    top = real_ctx.settings.max_qty_per_order
    nasty = ",".join(["", ":", "::", f"{black.id}:-5", f"{black.id}:3", "var_nope:2", "<script>alert(1)</script>:1",
                      f"{natural.id}:9999999999999999999999", "x" * 300 + ":1", f"{natural.id}"])
    page = web.get(f"/stores/{seller.id}/cart", headers=raw_cart(seller, nasty))
    assert page.status_code == 200 and "<script>alert" not in page.text
    assert "Rp85.000 × 1 = Rp85.000" in page.text                    # the first copy of a variant wins, a bad quantity becomes 1
    assert f"Rp150.000 × {top} = {rupiah(150_000 * top)}" in page.text
    assert page.text.count("class=\"cartline\"") == 2                 # the unknown ids are dropped, nothing else appears


def test_a_long_cookie_is_cut_and_a_cart_never_has_more_than_the_maximum_lines(web):
    seller, hijab, bag = open_shop()
    many = ",".join(f"var_{i}:1" for i in range(40))
    assert storefront._read_cart(real_ctx, many) == [(f"var_{i}", 1) for i in range(storefront.CART_MAX_LINES)]
    assert storefront._read_cart(real_ctx, "a" * 5000 + ":1") == []
    assert storefront._read_cart(real_ctx, None) == [] and storefront._read_cart(real_ctx, "") == []
    assert web.get(f"/stores/{seller.id}/cart", headers=raw_cart(seller, many)).status_code == 200


def test_each_shop_has_its_own_cart_and_one_shops_cookie_cannot_carry_another_shops_option(web):
    a, hijab_a, _ = open_shop()
    b, hijab_b, _ = open_shop()
    black_a, black_b = variant(hijab_a, "Black"), variant(hijab_b, "Black")
    add(web, a, black_a)
    add(web, b, black_b, 3)
    assert cookie_name(a) != cookie_name(b)
    assert hijab_a.title in web.get(f"/stores/{a.id}/cart").text and hijab_b.title not in web.get(f"/stores/{a.id}/cart").text
    assert badge(web.get(f"/stores/{a.id}").text) == 1 and badge(web.get(f"/stores/{b.id}").text) == 3
    smuggled = web.get(f"/stores/{a.id}/cart", headers=raw_cart(a, f"{black_b.id}:2"))       # B's option in A's cookie
    assert "Your cart is empty" in smuggled.text
    assert checkout(web, a, order_form(), cookie=f"{cookie_name(a)}={black_b.id}:2").headers["location"] == f"/stores/{a.id}/cart"
    assert real_ctx.store.list_orders(b.id) == []


# ---------------------------------------------------------------- checking out
def test_the_cart_checkout_makes_one_order_for_every_line_and_empties_the_cart(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    add(web, seller, black, 2)
    add(web, seller, natural)
    form = web.get(f"/stores/{seller.id}/cart/checkout")
    assert form.status_code == 200 and "Place order" in form.text and hijab.title in form.text and bag.title in form.text
    assert "Contact" in form.text and "Delivery address" in form.text and "Rp320.000" in form.text
    r = checkout(web, seller)
    assert r.status_code == 303 and r.headers["location"].startswith("/pay/")
    orders = real_ctx.store.list_orders(seller.id)
    assert len(orders) == 1                                                                 # one order, not one per line
    order = orders[0]
    assert r.headers["location"] == f"/pay/{order.id}"
    assert sorted((i.variant_id, i.qty) for i in order.items) == sorted([(black.id, 2), (natural.id, 1)])
    assert order.subtotal == 2 * 85_000 + 150_000 and order.channel == "web"
    assert order.status in (OrderStatus.AWAITING_PAYMENT, OrderStatus.AWAITING_SELLER_CONFIRMATION)
    reserved = {v.id: v.stock.reserved for p in (real_ctx.store.get_product(hijab.id), real_ctx.store.get_product(bag.id)) for v in p.variants}
    assert reserved[black.id] == 2 and reserved[natural.id] == 1                            # every line holds its own stock
    assert f"{cookie_name(seller)}=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    assert "Your cart is empty" in web.get(f"/stores/{seller.id}/cart").text
    assert hijab.title in web.get(r.headers["location"]).text


def test_an_order_with_a_mistake_in_the_form_keeps_the_cart_and_the_answers(web):
    seller, hijab, _ = open_shop()
    add(web, seller, variant(hijab, "Black"))
    r = checkout(web, seller, order_form(postal="4013"))
    assert r.status_code == 422 and "Postal code must be 5 digits" in r.text and "Dewi Lestari" in r.text
    assert real_ctx.store.list_orders(seller.id) == []
    assert "Rp85.000 × 1" in web.get(f"/stores/{seller.id}/cart").text                      # still there to try again
    assert checkout(web, seller).status_code == 303


def test_checkout_is_all_or_nothing_when_one_line_cannot_be_held(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    add(web, seller, black, 2)
    add(web, seller, natural, 3)
    real_ctx.store.set_stock(seller.id, bag.id, natural.id, 2, "test")                       # 3 in the cart, 2 left
    r = checkout(web, seller)
    assert r.status_code == 422 and real_ctx.store.list_orders(seller.id) == []
    assert all(v.stock.reserved == 0 for v in real_ctx.store.get_product(hijab.id).variants)  # the hijabs were not held either
    assert "Rp85.000 × 2" in web.get(f"/stores/{seller.id}/cart").text                       # and the cart is kept


def test_a_sold_out_line_blocks_checkout_and_the_cart_says_which(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    add(web, seller, black)
    add(web, seller, natural)
    real_ctx.store.set_stock(seller.id, bag.id, natural.id, 0, "test")
    page = web.get(f"/stores/{seller.id}/cart").text
    assert "Sold out" in page and "Remove them to continue" in page and "<button class=btn type=button disabled>Checkout</button>" in page
    assert web.get(f"/stores/{seller.id}/cart/checkout", follow_redirects=False).headers["location"] == f"/stores/{seller.id}/cart"
    assert checkout(web, seller).headers["location"] == f"/stores/{seller.id}/cart"
    assert real_ctx.store.list_orders(seller.id) == []
    change(web, seller, "remove", natural)
    assert checkout(web, seller).status_code == 303 and len(real_ctx.store.list_orders(seller.id)) == 1


def test_a_product_the_seller_took_down_just_drops_out_of_the_cart_and_out_of_the_order(web):
    seller, hijab, bag = open_shop()
    black, natural = variant(hijab, "Black"), bag.variants[0]
    add(web, seller, black)
    add(web, seller, natural)
    gone = real_ctx.store.get_product(bag.id)
    gone.status = ProductStatus.DRAFT
    real_ctx.store.put_product(gone)
    page = web.get(f"/stores/{seller.id}/cart")
    assert page.status_code == 200 and hijab.title in page.text and bag.title not in page.text
    assert checkout(web, seller).status_code == 303
    assert [i.variant_id for i in real_ctx.store.list_orders(seller.id)[0].items] == [black.id]


def test_a_shop_that_is_not_verified_has_no_cart_at_all(web):
    seller, hijab, _ = open_shop(kyc="pending")
    black = variant(hijab, "Black")
    for r in (web.get(f"/stores/{seller.id}/cart"), web.get(f"/stores/{seller.id}/cart/checkout"),
              web.post(f"/stores/{seller.id}/cart/add", content=f"variant_id={black.id}&qty=1", headers=FORM),
              checkout(web, seller, cookie=f"{cookie_name(seller)}={black.id}:1")):
        assert r.status_code == 404 and "This shop is not open yet" in r.text
        assert seller.name not in r.text and "Place order" not in r.text and "set-cookie" not in r.headers
    assert real_ctx.store.list_orders(seller.id) == []
    assert web.get("/stores/sel_nope/cart").status_code == 404


# ---------------------------------------------------------------- cross-site posts
def test_a_cross_site_post_can_neither_fill_the_cart_nor_place_an_order(web):
    seller, hijab, _ = open_shop()
    black = variant(hijab, "Black")
    for origin in ("https://evil.example", "null"):
        assert add(web, seller, black, origin=origin).status_code == 403
        assert web.post(f"/stores/{seller.id}/cart/remove", content=f"variant_id={black.id}", headers={**FORM, "origin": origin}).status_code == 403
    assert cookie_name(seller) not in web.cookies.keys()
    ok = add(web, seller, black, origin="http://testserver")
    assert ok.status_code == 303
    assert checkout(web, seller, origin="https://evil.example").status_code == 403
    assert real_ctx.store.list_orders(seller.id) == []
    assert checkout(web, seller, origin="http://testserver").status_code == 303


def test_the_cart_forms_post_to_the_pages_own_address_so_the_content_security_policy_allows_them(web):
    seller, hijab, _ = open_shop()
    add(web, seller, variant(hijab, "Black"))
    for url in (f"/stores/{seller.id}/cart", f"/stores/{seller.id}/cart/checkout"):
        r = web.get(url)
        assert "form-action 'self'" in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "same-origin"
        assert all(a.startswith("/stores/") for a in re.findall(r'<form[^>]* action="([^"]+)"', r.text))


# ---------------------------------------------------------------- from the product page
def test_the_product_page_offers_add_to_cart_next_to_buy_now_and_the_chips_choose_the_option(web):
    seller, hijab, _ = open_shop()
    page = web.get(f"/stores/{seller.id}/p/{hijab.id}")
    assert page.status_code == 200 and "Add to cart" in page.text and "Buy now" in page.text
    assert f'action="/stores/{seller.id}/cart/add"' in page.text and "data-variant-input" in page.text
    navy = variant(hijab, "Navy")
    chosen = web.get(f"/stores/{seller.id}/p/{hijab.id}?v={navy.id}").text
    assert re.search(rf'name=variant_id value="{navy.id}" data-variant-input', chosen)
    add_one = web.post(f"/stores/{seller.id}/cart/add", content=f"variant_id={navy.id}&qty=1", headers=FORM, follow_redirects=False)
    assert add_one.status_code == 303 and badge(web.get(f"/stores/{seller.id}/p/{hijab.id}").text) == 1


def test_a_sold_out_product_page_offers_no_add_to_cart(web):
    seller, _, bag = open_shop()
    real_ctx.store.set_stock(seller.id, bag.id, bag.variants[0].id, 0, "test")
    page = web.get(f"/stores/{seller.id}/p/{bag.id}").text
    assert "Sold out" in page and "Add to cart" not in page


def test_the_cart_works_on_a_shops_own_address(web, monkeypatch):
    monkeypatch.setattr(real_ctx.settings, "store_base_domain", "etalase.test")
    from etalase.seller import slugs
    seller, hijab, _ = open_shop()
    slugs.assign(real_ctx, seller.id)
    seller = real_ctx.store.get_seller(seller.id)
    host = f"{seller.slug}.etalase.test"
    black = variant(hijab, "Black")
    r = web.post(f"/stores/{seller.id}/cart/add", content=f"variant_id={black.id}&qty=1",
                 headers={**FORM, "host": host, "origin": f"http://{host}"}, follow_redirects=False)
    assert r.status_code == 303
    page = web.get(f"/stores/{seller.id}/cart", headers={"host": host})
    assert page.status_code == 200 and "Rp85.000 × 1" in page.text
    other, _, _ = open_shop()
    assert web.get(f"/stores/{other.id}/cart", headers={"host": host}).status_code == 404     # another shop's cart is not on this address


def test_the_cart_button_alone_is_served_for_the_page_script_and_follows_the_cart(web):
    seller, hijab, _ = open_shop()
    empty = web.get(f"/stores/{seller.id}/cart/badge")
    assert empty.status_code == 200 and empty.headers["cache-control"] == "no-store"
    assert empty.text.startswith("<a ") and "cartlink" in empty.text and "cartn" not in empty.text and 'aria-label="Cart"' in empty.text
    add(web, seller, variant(hijab, "Black"), 3)
    full = web.get(f"/stores/{seller.id}/cart/badge").text
    assert badge(full) == 3 and 'aria-label="Cart, 3 items"' in full and "<html" not in full
    assert web.get("/stores/sel_nope/cart/badge").status_code == 404
    pending, _, _ = open_shop(kyc="pending")
    shut = web.get(f"/stores/{pending.id}/cart/badge")
    assert shut.status_code == 404 and shut.text == ""                                       # a shop that is not open shows nothing
