"""UCP business server: the profile and every checkout response are validated against the official SDK models
(ucp-sdk, generated from the UCP JSON schemas), and the whole flow runs end to end on the mock adapters."""

import pytest
from fastapi.testclient import TestClient
from ucp_sdk.models.schemas.shopping import checkout as sdk_checkout
from ucp_sdk.models.schemas.shopping.payment import Payment
from ucp_sdk.models.schemas.ucp import BusinessSchema

from etalase.api.main import app, ctx
from etalase.gateway import ucp as ucp_protocol

from .conftest import make_product, make_seller, variant

sdk_checkout.Checkout.model_rebuild(_types_namespace={"Payment": Payment})
HEADERS = {"UCP-Agent": 'profile="https://platform.example/profile"'}
API = "/ucp/v1"
BUYER = {"first_name": "Dewi", "last_name": "Lestari", "phone_number": "6285700001111"}
_phones = iter(range(6285710000000, 6285719999999))   # Etalase limits checkouts per phone number per hour; tests need many buyers


def fresh_buyer(**overrides):
    return {**BUYER, "phone_number": str(next(_phones)), **overrides}
DEST = {"street_address": "Jl. Dago 10", "address_locality": "Bandung", "address_region": "Jawa Barat",
        "postal_code": "40135", "address_country": "ID"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)   # no lifespan needed: nothing here touches /mcp or the expiry loop


@pytest.fixture
def item(client):
    seller = make_seller(ctx, name=f"UCP Shop {len(ctx.store.list_products())}", wa=f"62812{len(ctx.store.list_products()):08d}")
    product = make_product(ctx, seller)
    return product, variant(product, "Black")


def post(client, path, body=None, key=None, headers=None):
    h = {**HEADERS, **(headers or {})}
    if key:
        h["Idempotency-Key"] = key
    return client.post(f"{API}{path}", json=body if body is not None else {}, headers=h)


def valid(response):
    """The response must parse as an official UCP Checkout."""
    data = response.json()
    sdk_checkout.Checkout.model_validate(data)
    return data


def new_session(client, v, qty=2):
    r = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": qty}]})
    assert r.status_code == 201, r.text
    return valid(r)


def ready_session(client, v, qty=2):
    s = new_session(client, v, qty)
    buyer = fresh_buyer()
    r = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "line_items": [{"id": "li_1", "item": {"id": v.id}, "quantity": qty}], "buyer": buyer,
        "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST]}]}})
    s = valid(r)
    option = s["fulfillment"]["methods"][0]["groups"][0]["options"][0]["id"]
    r = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST],
                                                      "groups": [{"selected_option_id": option}]}]}})
    return valid(r)


# ------------------------------------------------------------------ discovery
def test_profile_is_a_valid_ucp_business_profile(client):
    profile = client.get("/.well-known/ucp").json()
    BusinessSchema(**profile["ucp"])
    assert profile["ucp"]["version"] == ucp_protocol.UCP_VERSION
    service = profile["ucp"]["services"]["dev.ucp.shopping"][0]
    assert service["transport"] == "rest" and service["endpoint"].endswith(API)
    caps = profile["ucp"]["capabilities"]
    assert set(caps) == {"dev.ucp.shopping.checkout", "dev.ucp.shopping.fulfillment", "dev.ucp.shopping.buyer_consent"}
    assert caps["dev.ucp.shopping.fulfillment"][0]["extends"] == "dev.ucp.shopping.checkout"
    assert caps["dev.ucp.shopping.buyer_consent"][0]["extends"] == "dev.ucp.shopping.checkout"


# ------------------------------------------------------------------ the whole journey
def test_checkout_flow_from_empty_session_to_paid_order(client, item):
    product, v = item
    s = new_session(client, v)
    assert s["status"] == "incomplete" and s["currency"] == "IDR"
    assert s["line_items"][0]["item"]["price"] == 85000 * 100          # ISO 4217 minor units
    assert {m["path"] for m in s["messages"]} >= {"$.buyer.first_name", "$.buyer.phone_number",
                                                  "$.fulfillment.methods[0].destinations"}

    updated = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": BUYER, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST]}]}})
    s = valid(updated)
    group = s["fulfillment"]["methods"][0]["groups"][0]
    assert len(group["options"]) == 3 and "selected_option_id" not in group
    assert s["status"] == "incomplete"
    assert [m["code"] for m in s["messages"]] == ["missing"]
    assert s["messages"][0]["path"].endswith("selected_option_id")

    s = ready_session(client, v)
    assert s["status"] == "ready_for_complete" and not s.get("messages")
    assert {t["type"] for t in s["totals"]} == {"subtotal", "fulfillment", "total"}
    assert s["totals"][-1]["amount"] == (85000 * 2 + 12000) * 100       # cheapest courier (jne REG, 1 kg)
    assert s["continue_url"].endswith(f"/checkout/{s['id']}")
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 0   # nothing held before complete

    done = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    assert done["status"] == "requires_escalation" and "order" not in done
    assert done["messages"][0]["code"] == "payment_required"
    assert done["messages"][0]["severity"] == "requires_buyer_review"
    assert done["continue_url"].endswith(f"/checkout/{s['id']}")
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 2

    page = client.get(f"/checkout/{s['id']}")
    assert page.status_code == 200 and "<svg" in page.text and "Rp 182.000" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]

    order = next(o for o in ctx.store.list_orders(product.seller_id))
    ctx.checkout.payment_succeeded(order.payment.reference)
    final = valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))
    assert final["status"] == "completed" and final["order"]["id"] == order.id
    assert "continue_url" not in final
    assert "Paid" in client.get(f"/checkout/{s['id']}").text


def test_completing_twice_creates_one_order_and_incomplete_sessions_cannot_complete(client, item):
    product, v = item
    not_ready = valid(post(client, f"/checkout-sessions/{new_session(client, v)['id']}/complete", {}))
    assert not_ready["status"] == "incomplete" and not_ready["messages"][-1]["code"] == "not_ready"
    assert ctx.store.list_orders(product.seller_id) == []

    s = ready_session(client, v)
    first = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    second = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    assert first["status"] == second["status"] == "requires_escalation"
    assert len(ctx.store.list_orders(product.seller_id)) == 1
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 2


def test_confirm_first_sellers_keep_the_session_in_progress_until_stock_is_confirmed(client):
    seller = make_seller(ctx, name="Confirm Shop", wa="6281299990001")
    product = make_product(ctx, seller)            # stock never confirmed by hand: the seller is asked first
    v = variant(product, "Navy")
    ctx.store.set_stock(seller.id, product.id, v.id, None, "chat")        # untracked stock = confirm first
    s = ready_session(client, v, qty=1)
    waiting = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    assert waiting["status"] == "complete_in_progress" and waiting["messages"][0]["code"] == "seller_confirms_stock"
    order = ctx.store.list_orders(seller.id)[0]
    ctx.checkout.seller_confirms_stock(seller.id, order.id, available=True)
    pay = valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))
    assert pay["status"] == "requires_escalation" and pay["messages"][0]["code"] == "payment_required"
    assert "<svg" in client.get(f"/checkout/{s['id']}").text


def test_rejected_confirmation_cancels_the_session(client):
    seller = make_seller(ctx, name="Reject Shop", wa="6281299990002")
    product = make_product(ctx, seller)
    v = variant(product, "Navy")
    ctx.store.set_stock(seller.id, product.id, v.id, None, "chat")
    s = ready_session(client, v, qty=1)
    post(client, f"/checkout-sessions/{s['id']}/complete", {})
    ctx.checkout.seller_confirms_stock(seller.id, ctx.store.list_orders(seller.id)[0].id, available=False)
    assert valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))["status"] == "canceled"


def test_cancel_gives_the_stock_back_and_is_repeatable(client, item):
    product, v = item
    s = ready_session(client, v, qty=3)
    post(client, f"/checkout-sessions/{s['id']}/complete", {})
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 3
    cancelled = valid(post(client, f"/checkout-sessions/{s['id']}/cancel"))
    assert cancelled["status"] == "canceled" and "continue_url" not in cancelled
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 0
    assert valid(post(client, f"/checkout-sessions/{s['id']}/cancel"))["status"] == "canceled"


def test_a_paid_order_cannot_be_cancelled_through_the_protocol(client, item):
    product, v = item
    s = ready_session(client, v)
    post(client, f"/checkout-sessions/{s['id']}/complete", {})
    ctx.checkout.payment_succeeded(ctx.store.list_orders(product.seller_id)[0].payment.reference)
    r = post(client, f"/checkout-sessions/{s['id']}/cancel")
    assert r.status_code == 409 and r.json()["messages"][0]["code"] == "cannot_cancel"
    assert valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))["status"] == "completed"
    # a closed checkout cannot be changed, completed again, or edited
    assert post(client, f"/checkout-sessions/{s['id']}/complete", {}).status_code == 409
    assert client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": BUYER}).status_code == 409


def test_closed_checkouts_answer_409(client, item):
    _, v = item
    s = new_session(client, v)
    post(client, f"/checkout-sessions/{s['id']}/cancel")
    assert client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": BUYER}).status_code == 409
    assert post(client, f"/checkout-sessions/{s['id']}/complete", {}).status_code == 409


def test_destination_ids_chosen_by_the_platform_are_kept(client, item):
    _, v = item
    s = new_session(client, v)
    r = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": BUYER, "fulfillment": {"methods": [{"type": "shipping", "destinations": [{**DEST, "id": "dest_2"}]}]}})
    method = valid(r)["fulfillment"]["methods"][0]
    assert method["selected_destination_id"] == "dest_2" and method["destinations"][0]["id"] == "dest_2"


def test_update_after_complete_is_refused_without_changing_anything(client, item):
    _, v = item
    s = ready_session(client, v)
    post(client, f"/checkout-sessions/{s['id']}/complete", {})
    r = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": {**BUYER, "first_name": "Eve"}}))
    assert r["status"] == "requires_escalation" and r["messages"][-1]["code"] == "not_editable"
    assert r["buyer"]["first_name"] == "Dewi"


# ------------------------------------------------------------------ input handling
def test_validation_problems_are_reported_as_messages_not_crashes(client, item):
    _, v = item
    s = new_session(client, v)
    bad = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": {**BUYER, "phone_number": "12"},
        "fulfillment": {"methods": [{"type": "shipping", "destinations": [{**DEST, "postal_code": "ABCDE"}]}]}})
    data = valid(bad)
    assert data["status"] == "incomplete"
    assert {m["code"] for m in data["messages"]} >= {"invalid"}

    abroad = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": BUYER, "fulfillment": {"methods": [{"type": "shipping", "destinations": [{**DEST, "address_country": "SG"}]}]}}))
    assert any(m["code"] == "unsupported_destination" for m in abroad["messages"])


def test_unavailable_items_return_an_error_envelope_and_create_nothing(client, item):
    _, v = item
    unknown = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": "var_nope"}, "quantity": 1}]})
    assert unknown.status_code == 200 and unknown.json()["ucp"]["status"] == "error"
    assert unknown.json()["messages"][0]["code"] == "item_unavailable"
    many = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}] * (ucp_protocol.MAX_LINES + 1)})
    assert many.status_code == 200 and many.json()["messages"][0]["code"] == "unsupported"


def test_protocol_errors(client, item):
    _, v = item
    body = {"line_items": [{"item": {"id": v.id}, "quantity": 1}]}
    assert client.post(f"{API}/checkout-sessions", json=body).status_code == 400                 # no UCP-Agent
    old = {"UCP-Agent": 'profile="https://p.example/profile"; version="2099-01-01"'}
    assert client.post(f"{API}/checkout-sessions", json=body, headers=old).status_code == 422
    new = {"UCP-Agent": f'profile="https://p.example/profile"; version="{ucp_protocol.UCP_VERSION}"'}
    assert client.post(f"{API}/checkout-sessions", json=body, headers=new).status_code == 201
    assert post(client, "/checkout-sessions", {"line_items": []}).status_code == 422
    assert post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 0}]}).status_code == 422
    assert client.post(f"{API}/checkout-sessions", content=b"{nope", headers=HEADERS).status_code == 400
    missing = client.get(f"{API}/checkout-sessions/chk_missing", headers=HEADERS)
    assert missing.status_code == 404 and missing.json()["ucp"]["status"] == "error"


def test_idempotency_replays_the_same_answer_and_rejects_a_different_request(client, item):
    _, v = item
    body = {"line_items": [{"item": {"id": v.id}, "quantity": 1}]}
    first = post(client, "/checkout-sessions", body, key="k-create-1")
    again = post(client, "/checkout-sessions", body, key="k-create-1")
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
    other = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 2}]}, key="k-create-1")
    assert other.status_code == 409 and other.json()["messages"][0]["code"] == "idempotency_conflict"


def test_pay_page_escapes_everything_and_unknown_ids_are_harmless(client, item):
    product, v = item
    product.title = '<script>alert(1)</script> Hijab'
    ctx.store.put_product(product)
    s = ready_session(client, v)
    post(client, f"/checkout-sessions/{s['id']}/complete", {})
    page = client.get(f"/checkout/{s['id']}").text
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "not valid" in client.get("/checkout/chk_unknown").text
    assert client.get("/checkout/" + "x" * 500).status_code == 200


def test_sold_out_item_cannot_start_a_session(client, item):
    product, v = item
    ctx.store.set_stock(product.seller_id, product.id, v.id, 0, "test")
    r = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}]})
    assert r.json()["messages"][0]["code"] == "out_of_stock"


def test_checkout_links_to_the_sellers_return_policy(client, item):
    product, v = item
    data = new_session(client, v)       # valid() has already checked the response against the official UCP models
    base = ctx.settings.public_base_url
    assert data["links"] == [{"type": "refund_policy", "title": "Return policy", "url": f"{base}/stores/{product.seller_id}/returns"}]
    page = client.get(f"/stores/{product.seller_id}/returns")
    assert page.status_code == 200 and "168 hours" in page.text


def test_a_returned_and_refunded_order_stays_completed_and_the_pay_page_does_not_say_nothing_was_charged(client, item):
    product, v = item
    s = ready_session(client, v, 1)
    valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    order = ctx.store.list_orders(product.seller_id)[0]
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(product.seller_id, order.id)
    ctx.checkout.mark_shipped(product.seller_id, order.id, "JNE919191")
    ctx.checkout.shipment_update("JNE919191", "delivered")
    phone = ctx.store.get_order(order.id).buyer.phone
    ctx.returns.request(order.id, phone, "damaged", "torn")
    ctx.returns.seller_responds(product.seller_id, order.id, approve=True)
    ctx.returns.seller_received(product.seller_id, order.id)
    assert ctx.store.get_order(order.id).status.value == "refunded"
    final = valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))
    assert final["status"] == "completed"
    page = client.get(f"/checkout/{s['id']}").text
    assert "refunded" in page and "Nothing was charged" not in page


# ------------------------------------------------------------------ carts: several line items in one session
@pytest.fixture
def cart_shop():
    """One open shop with two products, so a cart has something to hold."""
    n = len(ctx.store.list_products())
    seller = make_seller(ctx, name=f"UCP Cart {n}", wa=f"62814{n:08d}")
    voal = make_product(ctx, seller, "Voal hijab, black 5, navy 3, mocca 10, price 85k")
    pashmina = make_product(ctx, seller, "Pashmina silk, pink 4, cream 2, price 145k")
    return seller, voal, pashmina


def lines_of(*pairs):
    return [{"item": {"id": v.id}, "quantity": q} for v, q in pairs]


def ready_cart(client, line_items):
    r = post(client, "/checkout-sessions", {"line_items": line_items})
    assert r.status_code == 201, r.text
    s = valid(r)
    buyer = fresh_buyer()
    s = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST]}]}}))
    option = s["fulfillment"]["methods"][0]["groups"][0]["options"][0]["id"]
    return valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={
        "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST],
                                                      "groups": [{"selected_option_id": option}]}]}}))


def test_a_cart_of_two_items_becomes_one_order_priced_the_way_the_session_said(client, cart_shop):
    seller, voal, pashmina = cart_shop
    black, pink = variant(voal, "Black"), variant(pashmina, "Pink")
    s = ready_cart(client, lines_of((black, 2), (pink, 1)))
    assert [li["id"] for li in s["line_items"]] == ["li_1", "li_2"] and [li["quantity"] for li in s["line_items"]] == [2, 1]
    assert s["line_items"][1]["item"]["price"] == 145000 * 100
    assert s["status"] == "ready_for_complete" and not s.get("messages")
    assert s["totals"][0] == {"type": "subtotal", "amount": (85000 * 2 + 145000) * 100}
    method = s["fulfillment"]["methods"][0]
    assert method["line_item_ids"] == ["li_1", "li_2"] and method["groups"][0]["line_item_ids"] == ["li_1", "li_2"]
    quoted = s["totals"][-1]["amount"]
    assert quoted > s["totals"][0]["amount"]                        # shipping is in the total

    done = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    assert done["status"] == "requires_escalation" and done["messages"][0]["code"] == "payment_required"
    order = ctx.store.list_orders(seller.id)[0]
    assert [(i.variant_id, i.qty) for i in order.items] == [(black.id, 2), (pink.id, 1)]
    assert order.total * 100 == quoted                              # the order costs what the session quoted
    stock = lambda prod, v: ctx.store.get_product(prod.id).variant(v.id).stock.reserved   # noqa: E731
    assert (stock(voal, black), stock(pashmina, pink)) == (2, 1)    # both lines held together

    page = client.get(f"/checkout/{s['id']}").text
    assert "Voal" in page and "Pashmina" in page and "Shipping" in page and "Total" in page
    assert f"Rp {order.total:,}".replace(",", ".") in page


def test_an_update_with_line_items_replaces_the_cart_and_one_without_keeps_it(client, cart_shop):
    _, voal, pashmina = cart_shop
    black, navy, pink = variant(voal, "Black"), variant(voal, "Navy"), variant(pashmina, "Pink")
    s = ready_cart(client, lines_of((black, 1)))
    assert s["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"]
    kept = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": fresh_buyer()}))
    assert [li["item"]["id"] for li in kept["line_items"]] == [black.id]
    swapped = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS,
                               json={"line_items": lines_of((navy, 1), (pink, 2))}))
    assert [(li["item"]["id"], li["quantity"]) for li in swapped["line_items"]] == [(navy.id, 1), (pink.id, 2)]
    assert "selected_option_id" not in swapped["fulfillment"]["methods"][0]["groups"][0]     # a new cart is a new quote
    assert swapped["status"] == "incomplete"
    gone = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"line_items": lines_of((navy, 1)) + [
        {"item": {"id": "var_nope"}, "quantity": 1}]})
    assert gone.status_code == 422 and gone.json()["messages"][0]["code"] == "item_unavailable"
    still = valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))
    assert [li["item"]["id"] for li in still["line_items"]] == [navy.id, pink.id]          # the refused update changed nothing


def test_the_same_variant_twice_is_one_line(client, cart_shop):
    _, voal, _ = cart_shop
    black = variant(voal, "Black")
    s = valid(post(client, "/checkout-sessions", {"line_items": lines_of((black, 1), (black, 2))}))
    assert [(li["item"]["id"], li["quantity"]) for li in s["line_items"]] == [(black.id, 3)]


def test_a_cart_cannot_mix_shops(client, cart_shop, item):
    _, voal, _ = cart_shop
    _, other = item
    mixed = post(client, "/checkout-sessions", {"line_items": lines_of((variant(voal, "Black"), 1), (other, 1))})
    assert mixed.status_code == 200 and mixed.json()["ucp"]["status"] == "error"
    assert mixed.json()["messages"][0]["code"] == "unsupported" and "same shop" in mixed.json()["messages"][0]["content"]
    s = new_session(client, variant(voal, "Black"), 1)
    r = client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"line_items": lines_of((variant(voal, "Black"), 1), (other, 1))})
    assert r.status_code == 422 and "same shop" in r.json()["messages"][0]["content"]


def test_a_line_that_is_short_of_stock_is_flagged_at_its_own_index(client, cart_shop):
    _, voal, pashmina = cart_shop
    black, cream = variant(voal, "Black"), variant(pashmina, "Cream")        # two left
    s = valid(post(client, "/checkout-sessions", {"line_items": lines_of((black, 1), (cream, 3))}))
    short = [m for m in s["messages"] if m["code"] == "out_of_stock"]
    assert len(short) == 1 and short[0]["path"] == "$.line_items[1].quantity" and "Only 2 left" in short[0]["content"]
    assert "Pashmina" in short[0]["content"] and s["status"] == "incomplete"
    ctx.store.set_stock(pashmina.seller_id, pashmina.id, cream.id, 0, "test")
    refused = post(client, "/checkout-sessions", {"line_items": lines_of((black, 1), (cream, 1))})
    assert refused.json()["messages"][0]["code"] == "out_of_stock" and "Pashmina" in refused.json()["messages"][0]["content"]


def test_the_unit_cap_counts_the_whole_cart(client, cart_shop, monkeypatch):
    _, voal, pashmina = cart_shop
    monkeypatch.setattr(ctx.settings, "max_qty_per_order", 3)
    s = valid(post(client, "/checkout-sessions", {"line_items": lines_of((variant(voal, "Black"), 2), (variant(pashmina, "Pink"), 2))}))
    capped = [m for m in s["messages"] if m["code"] == "quantity_exceeded"]
    assert len(capped) == 1 and capped[0]["path"] == "$.line_items[1].quantity"       # said once, where the cart crosses the cap
    assert s["status"] == "incomplete"


def test_a_session_from_before_carts_still_loads_and_can_be_updated(client, cart_shop):
    _, voal, _ = cart_shop
    black = variant(voal, "Black")
    s = new_session(client, black, 2)
    stored = ctx.store.get_doc(ucp_protocol.SESSION_KIND, s["id"])
    lines = stored.pop("lines")
    stored.update(product_id=lines[0]["product_id"], variant_id=lines[0]["variant_id"], quantity=lines[0]["quantity"])
    ctx.store.put_doc(ucp_protocol.SESSION_KIND, s["id"], stored)                      # the old shape: one product, variant and quantity
    loaded = valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))
    assert [(li["item"]["id"], li["quantity"]) for li in loaded["line_items"]] == [(black.id, 2)]
    assert client.get(f"/checkout/{s['id']}").status_code == 200                       # the pay page reads the old shape too
    updated = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": fresh_buyer()}))
    assert updated["line_items"][0]["quantity"] == 2
    assert "lines" in ctx.store.get_doc(ucp_protocol.SESSION_KIND, s["id"])


# ------------------------------------------------------------------ buyer consent
CONSENT = {"marketing": True, "analytics": False, "sale_of_data": False}


def test_consent_sent_on_create_comes_back_on_create_and_get(client, item):
    _, v = item
    r = post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}],
                                            "buyer": {"first_name": "Consent", "last_name": "Tester", "consent": CONSENT}})
    assert r.status_code == 201, r.text
    created = valid(r)
    assert created["buyer"]["consent"] == CONSENT                                      # false is kept: it is an answer, not an absence
    assert "dev.ucp.shopping.buyer_consent" in created["ucp"]["capabilities"]
    got = valid(client.get(f"{API}/checkout-sessions/{created['id']}", headers=HEADERS))
    assert got["buyer"]["consent"] == CONSENT


def test_an_update_that_leaves_consent_out_keeps_it_and_one_that_sends_it_replaces_it(client, item):
    _, v = item
    s = valid(post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}], "buyer": {"consent": CONSENT}}))
    kept = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": fresh_buyer()}))
    assert kept["buyer"]["consent"] == CONSENT and kept["buyer"]["first_name"] == "Dewi"
    withdrawn = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": {**fresh_buyer(), "consent": {"marketing": False}}}))
    assert withdrawn["buyer"]["consent"] == {"marketing": False}                       # replaced, so a buyer can withdraw it


def test_only_known_consent_states_with_true_or_false_are_kept(client, item):
    _, v = item
    s = valid(post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}],
                                                  "buyer": {"consent": {"marketing": "yes", "analytics": True, "preferences": 1, "telemetry": True,
                                                                        "sale_of_data": None}}}))
    assert s["buyer"]["consent"] == {"analytics": True}
    s = valid(post(client, "/checkout-sessions", {"line_items": [{"item": {"id": v.id}, "quantity": 1}], "buyer": {"consent": "all"}}))
    assert "consent" not in s.get("buyer", {})                                         # not an object: ignored


def test_a_session_without_consent_says_nothing_about_it(client, item):
    _, v = item
    s = new_session(client, v)
    assert "consent" not in s.get("buyer", {})
    s = ready_session(client, v)
    assert "consent" not in s["buyer"]


def test_consent_survives_the_whole_journey_to_a_paid_order(client, item):
    product, v = item
    s = ready_session(client, v)
    s = valid(client.put(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS, json={"buyer": {**fresh_buyer(), "consent": CONSENT}}))
    assert s["status"] == "ready_for_complete" and s["buyer"]["consent"] == CONSENT
    done = valid(post(client, f"/checkout-sessions/{s['id']}/complete", {}))
    assert done["status"] == "requires_escalation" and done["buyer"]["consent"] == CONSENT
    order = next(o for o in ctx.store.list_orders(product.seller_id))
    ctx.checkout.payment_succeeded(order.payment.reference)
    assert valid(client.get(f"{API}/checkout-sessions/{s['id']}", headers=HEADERS))["buyer"]["consent"] == CONSENT
