"""A shop's own address (rina.<domain>) is its own business for agents: its UCP profile, its checkout, its REST and MCP tools
only see that shop, links point back at the address, and a shop that is not verified shows nothing at all."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from ucp_sdk.models.schemas.ucp import BusinessSchema

from etalase.api.main import app
from etalase.api.main import ctx as app_ctx
from etalase.api.main import limits as app_limits
from etalase.api.main import mcp
from etalase.gateway.hosts import ShopScope
from etalase.seller import slugs

from .conftest import make_product, make_seller, variant

DOMAIN = "etalase.test"
UCP = {"UCP-Agent": 'profile="https://platform.example/profile"'}
_phones = iter(range(6285720000000, 6285729999999))
_runs = iter(range(10_000))


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", DOMAIN)
    monkeypatch.setattr(app_ctx.settings, "public_base_url", "http://app.etalase.test")
    app_limits._hits.clear()      # the per-address limits are shared with every other test in the run
    yield TestClient(app)
    app_limits._hits.clear()


def at(slug=None):
    return {"host": f"{slug}.{DOMAIN}" if slug else "app.etalase.test"}


def open_shop(name, wa, text="Voal hijab, black 5, navy 3, mocca 10, price 85k", kyc="verified"):
    seller = make_seller(app_ctx, name=name, wa=wa, kyc=kyc)
    slugs.assign(app_ctx, seller.id)
    product = make_product(app_ctx, seller, text)
    return app_ctx.store.get_seller(seller.id), product


def letters(n):
    """The app's catalog is shared by every test in the run, so each test sells words nobody else sells."""
    return "".join(chr(97 + int(d)) for d in str(n))


@pytest.fixture
def two_shops():
    n = letters(next(_runs))
    a, pa = open_shop(f"Profil Satu {n}", f"628772{next(_phones)}"[:13], f"Zaitun{n} hijab, black 5, navy 3, mocca 10, price 85k")
    b, pb = open_shop(f"Profil Dua {n}", f"628773{next(_phones)}"[:13], f"Pashm{n} silk, pink 4, cream 2, price 145k")
    return (a, pa), (b, pb)


def word(product):
    """The one word that only this test's product has (the first word of its title)."""
    return product.title.lower().split()[0]


def session_body(v, qty=1):
    return {"line_items": [{"item": {"id": v.id}, "quantity": qty}]}


# ---------------------------------------------------------------- the profile
def test_each_shop_serves_its_own_valid_profile(c, two_shops):
    (a, _), (b, _) = two_shops
    platform = c.get("/.well-known/ucp", headers=at()).json()
    mine = c.get("/.well-known/ucp", headers=at(a.slug)).json()
    other = c.get("/.well-known/ucp", headers=at(b.slug)).json()
    for profile in (platform, mine, other):
        BusinessSchema(**profile["ucp"])                  # all three are valid UCP business profiles
    endpoint = lambda p: p["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"]    # noqa: E731
    assert endpoint(platform) == "http://app.etalase.test/ucp/v1"
    assert endpoint(mine) == f"http://{a.slug}.{DOMAIN}/ucp/v1" and endpoint(other) == f"http://{b.slug}.{DOMAIN}/ucp/v1"
    assert mine["ucp"]["capabilities"] == platform["ucp"]["capabilities"]           # same protocol, different business


def test_without_a_base_domain_the_profile_is_the_platform_s(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", None)
    monkeypatch.setattr(app_ctx.settings, "public_base_url", "http://app.etalase.test")
    profile = TestClient(app).get("/.well-known/ucp", headers=at("someone")).json()
    assert profile["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"] == "http://app.etalase.test/ucp/v1"


# ---------------------------------------------------------------- UCP checkout on a shop's address
def test_a_shop_address_only_sells_that_shops_items(c, two_shops):
    (a, pa), (b, pb) = two_shops
    mine, theirs = variant(pa, "Black"), variant(pb, "Pink")
    ok = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **at(a.slug)}, json=session_body(mine))
    assert ok.status_code == 201
    session = ok.json()
    assert session["continue_url"].startswith(f"http://{a.slug}.{DOMAIN}/checkout/")        # the buyer is sent back to this shop
    assert session["links"][0]["url"] == f"http://{a.slug}.{DOMAIN}/stores/{a.id}/returns"
    refused = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **at(a.slug)}, json=session_body(theirs))
    assert refused.status_code == 200 and refused.json()["messages"][0]["code"] == "item_unavailable"
    # the platform host still sells everything, and keeps its own links
    both = [c.post("/ucp/v1/checkout-sessions", headers={**UCP, **at()}, json=session_body(v)) for v in (mine, theirs)]
    assert [r.status_code for r in both] == [201, 201]
    assert both[0].json()["continue_url"].startswith("http://app.etalase.test/checkout/")


def test_a_session_belongs_to_the_shop_it_was_started_on(c, two_shops):
    (a, pa), (b, _) = two_shops
    session = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **at(a.slug)}, json=session_body(variant(pa, "Black"))).json()
    path = f"/ucp/v1/checkout-sessions/{session['id']}"
    assert c.get(path, headers={**UCP, **at(a.slug)}).status_code == 200
    assert c.get(path, headers={**UCP, **at(b.slug)}).status_code == 404                      # another shop cannot read it...
    assert c.put(path, headers={**UCP, **at(b.slug)}, json={}).status_code == 404            # ...change it...
    assert c.post(path + "/cancel", headers={**UCP, **at(b.slug)}, json={}).status_code == 404   # ...or cancel it
    assert "not valid" in c.get(f"/checkout/{session['id']}", headers=at(b.slug)).text      # nor open its payment page
    assert "not valid" not in c.get(f"/checkout/{session['id']}", headers=at(a.slug)).text
    assert c.get(path, headers={**UCP, **at()}).status_code == 200                           # the platform host can


def test_an_idempotency_key_on_two_shops_is_two_requests(c, two_shops):
    (a, pa), (b, pb) = two_shops
    key = {"Idempotency-Key": "same-key-on-two-shops"}
    first = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **key, **at(a.slug)}, json=session_body(variant(pa, "Black")))
    second = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **key, **at(b.slug)}, json=session_body(variant(pb, "Pink")))
    assert first.status_code == second.status_code == 201 and first.json()["id"] != second.json()["id"]


# ---------------------------------------------------------------- REST agent API
def test_rest_search_and_products_are_scoped(c, two_shops):
    (a, pa), (b, pb) = two_shops
    everywhere = c.get("/agent/v1/search", params={"q": f"{word(pa)} {word(pb)}", "limit": 10}, headers=at()).json()
    assert {r["product_id"] for r in everywhere["results"]} >= {pa.id, pb.id}
    here = c.get("/agent/v1/search", params={"q": f"{word(pa)} {word(pb)}", "limit": 10}, headers=at(a.slug)).json()
    assert {r["product_id"] for r in here["results"]} == {pa.id}
    assert c.get(f"/agent/v1/products/{pa.id}", headers=at(a.slug)).json()["product_id"] == pa.id
    assert c.get(f"/agent/v1/products/{pb.id}", headers=at(a.slug)).json() == {"error": "product not found"}
    assert c.get(f"/agent/v1/products/{pb.id}", headers=at(b.slug)).json()["product_id"] == pb.id
    shipping = c.get(f"/agent/v1/products/{pb.id}/shipping-options",
                     params={"variant_id": variant(pb, "Pink").id, "quantity": 1, "postal_code": "40135"}, headers=at(a.slug))
    assert shipping.json() == {"error": "product not found"}


def test_rest_checkout_and_status_are_scoped(c, two_shops):
    (a, pa), (b, pb) = two_shops
    body = {"product_id": pb.id, "variant_id": variant(pb, "Pink").id, "quantity": 1, "buyer_name": "Dewi",
            "buyer_phone": str(next(_phones)), "address_line": "Jl. Dago 10", "city": "Bandung", "postal_code": "40135"}
    assert c.post("/agent/v1/checkouts", json=body, headers=at(a.slug)).json() == {"error": "product not found"}
    order = c.post("/agent/v1/checkouts", json=body, headers=at(b.slug)).json()
    assert order["checkout_id"]
    assert c.get(f"/agent/v1/checkouts/{order['checkout_id']}", headers=at(a.slug)).json() == {"error": "checkout not found"}
    assert c.get(f"/agent/v1/checkouts/{order['checkout_id']}", headers=at(b.slug)).json()["checkout_id"] == order["checkout_id"]
    page = f"/pay/{order['checkout_id']}"
    assert c.get(page, headers=at(a.slug)).status_code == 404 and c.get(page, headers=at(b.slug)).status_code == 200


# ---------------------------------------------------------------- MCP tools
def call_tool(name, shop, **args):
    """Call an MCP tool the way FastMCP does for a request that arrived on a shop's address (no HTTP server needed)."""
    scope = {"type": "http", "headers": [], "client": ("203.0.113.9", 1234), "state": {"shop": shop} if shop else {}}
    fake = SimpleNamespace(request_context=SimpleNamespace(request=Request(scope)))
    return mcp._tool_manager._tools[name].fn(mcp_ctx=fake, **args)


def test_mcp_tools_only_see_the_shop_whose_address_they_were_called_on(two_shops):
    (a, pa), (b, pb) = two_shops
    on_a = ShopScope(a.id, f"http://{a.slug}.{DOMAIN}")
    ids = lambda r: {x["product_id"] for x in r["results"]}                                # noqa: E731
    both = f"{word(pa)} {word(pb)}"
    assert ids(call_tool("search_products", on_a, query=both, limit=10)) == {pa.id}
    assert ids(call_tool("search_products", None, query=both, limit=10)) == {pa.id, pb.id}
    assert call_tool("get_product", on_a, product_id=pb.id) == {"error": "product not found"}
    assert call_tool("get_product", on_a, product_id=pa.id)["product_id"] == pa.id
    assert call_tool("get_shipping_options", on_a, product_id=pb.id, variant_id=variant(pb, "Pink").id, quantity=1,
                     postal_code="40135") == {"error": "product not found"}
    assert call_tool("create_checkout", on_a, product_id=pb.id, variant_id=variant(pb, "Pink").id, quantity=1,
                     buyer_name="Dewi", buyer_phone=str(next(_phones)), address_line="Jl. Dago 10", city="Bandung",
                     postal_code="40135") == {"error": "product not found"}


# ---------------------------------------------------------------- one shop's address never shows another shop
def test_a_shop_address_does_not_show_another_shops_pages(c, two_shops):
    (a, pa), (b, _) = two_shops
    assert c.get("/", headers=at(a.slug)).status_code == 200
    assert c.get(f"/stores/{a.id}/p/{pa.id}", headers=at(a.slug)).status_code == 200
    assert c.get(f"/stores/{b.id}", headers=at(a.slug)).status_code == 404                    # b's store under a's name
    assert c.get(f"/stores/{b.id}/returns", headers=at(a.slug)).status_code == 404
    assert c.get(f"/stores/{b.id}", headers=at()).status_code == 200                          # the platform host shows any shop


def test_the_store_page_points_agents_at_its_own_address(c, two_shops):
    (a, _), _ = two_shops
    page = c.get("/", headers=at(a.slug)).text
    assert f"http://{a.slug}.{DOMAIN}/mcp/" in page and f"http://{a.slug}.{DOMAIN}/.well-known/ucp" in page


# ---------------------------------------------------------------- a shop that is not verified shows nothing
@pytest.fixture
def pending():
    n = letters(next(_runs))
    seller, product = open_shop(f"Profil Tiga {n}", f"628774{next(_phones)}"[:13], f"Wolscarf{n} wool, grey 5, price 70k", kyc="pending")
    return seller, product


def test_a_pending_shop_is_invisible_to_agents(c, pending):
    seller, product = pending
    found = c.get("/agent/v1/search", params={"q": word(product), "limit": 10}, headers=at()).json()
    assert found["results"] == []
    assert c.get(f"/agent/v1/products/{product.id}", headers=at()).json() == {"error": "product not found"}
    r = c.post("/ucp/v1/checkout-sessions", headers={**UCP, **at()}, json=session_body(variant(product, "Grey")))
    assert r.json()["messages"][0]["code"] == "item_unavailable"
    assert c.get(f"/stores/{seller.id}/products.jsonld", headers=at()).json() == []
    assert c.get(f"/stores/{seller.id}/returns.json", headers=at()).status_code == 404
    assert c.get(f"/stores/{seller.id}/returns", headers=at()).status_code == 404
    assert call_tool("search_products", None, query=word(product), limit=10)["results"] == []
    assert call_tool("get_product", None, product_id=product.id) == {"error": "product not found"}


def test_a_pending_shop_s_own_address_is_a_holding_page_and_nothing_else(c, pending):
    seller, product = pending
    home = c.get("/", headers=at(seller.slug))
    assert home.status_code == 404 and "not open yet" in home.text
    assert seller.name not in home.text and product.title not in home.text and "noindex" in home.text      # names nothing, indexed nowhere
    for path in ("/.well-known/ucp", "/mcp/", f"/stores/{seller.id}/p/{product.id}", "/agent/v1/search?q=wool",
                 "/ucp/v1/checkout-sessions", f"/stores/{seller.id}/products.jsonld"):
        assert c.get(path, headers=at(seller.slug)).status_code == 404, path
    assert c.get(f"/stores/{seller.id}", headers=at()).status_code == 404                              # nor on the platform host
    assert product.title not in c.get(f"/stores/{seller.id}", headers=at()).text


def test_verifying_the_shop_opens_everything_at_once(c, pending):
    seller, product = pending
    seller.kyc_status = "verified"
    app_ctx.store.put_seller(seller)
    assert c.get("/", headers=at(seller.slug)).status_code == 200
    assert c.get("/.well-known/ucp", headers=at(seller.slug)).status_code == 200
    found = c.get("/agent/v1/search", params={"q": word(product), "limit": 10}, headers=at()).json()
    assert product.id in {r["product_id"] for r in found["results"]}


def test_the_order_service_refuses_an_unverified_seller_for_any_caller(ctx):
    from etalase.models import Address, CheckoutError
    seller = make_seller(ctx, "Belum Verifikasi", "6287720000009", kyc="pending")
    product = make_product(ctx, seller)
    buyer = Address(name="Dewi", phone="6285700001111", line1="Jl. Dago 10", city="Bandung", postal_code="40135")
    with pytest.raises(CheckoutError, match="not accepting orders"):
        ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, buyer)


# ---------------------------------------------------------------- a cart (`items`) is checked item by item
def cart(shop, items):
    """GatewayTools.create_checkout with several items. Not reachable from REST, MCP or UCP today; it must not become a way
    round the shop address or the verification gate the day one of them exposes it."""
    return app_ctx.gateway.create_checkout(items=items, buyer_name="Dewi", buyer_phone=str(next(_phones)),
                                           address_line="Jl. Dago 10", city="Bandung", postal_code="40135", shop=shop)


def line(product, color, qty=1):
    return {"product_id": product.id, "variant_id": variant(product, color).id, "qty": qty}


def test_a_cart_on_a_shop_address_cannot_hold_another_shops_item(two_shops):
    (a, pa), (b, pb) = two_shops
    on_a = ShopScope(a.id, f"http://{a.slug}.{DOMAIN}")
    assert cart(on_a, [line(pa, "Black"), line(pb, "Pink")]) == {"error": "product not found"}
    assert cart(on_a, [line(pb, "Pink")]) == {"error": "product not found"}
    assert cart(on_a, [(pb.id, variant(pb, "Pink").id, 1)]) == {"error": "product not found"}       # a tuple is no way round it
    order = cart(on_a, [line(pa, "Black"), line(pa, "Navy", 2)])
    assert order["checkout_id"] and [i["qty"] for i in order["items"]] == [1, 2]


def test_a_cart_on_the_platform_address_still_cannot_mix_shops(two_shops):
    (_, pa), (_, pb) = two_shops
    assert "same shop" in cart(None, [line(pa, "Black"), line(pb, "Pink")])["error"]
    assert cart(None, [line(pa, "Black"), line(pa, "Navy")])["checkout_id"]


def test_a_cart_cannot_hold_an_item_of_a_shop_that_is_not_open(two_shops, pending):
    (_, pa), _ = two_shops
    _, hidden = pending
    assert cart(None, [line(hidden, "Grey")]) == {"error": "product not found"}
    assert cart(None, [line(pa, "Black"), line(hidden, "Grey")]) == {"error": "product not found"}


def test_a_cart_with_no_items_or_a_malformed_one_is_refused(two_shops):
    (a, pa), _ = two_shops
    on_a = ShopScope(a.id, f"http://{a.slug}.{DOMAIN}")
    assert "at least one item" in cart(on_a, [])["error"]
    assert cart(on_a, ["not an item"]) == {"error": "product not found"}
    assert cart(on_a, [{"variant_id": "x"}]) == {"error": "product not found"}
