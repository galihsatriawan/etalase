"""A cart (several products of one shop in one order) through the agent doors: REST and MCP, and the shipping quote for it.
The shop rules still hold line by line: another shop's item is "not found" on a shop's address, one cart is one shop."""

import pytest

from etalase.api.main import ctx as app_ctx
from etalase.api.main import mcp
from etalase.gateway.hosts import ShopScope

from .conftest import variant
from .test_shop_profiles import DOMAIN, _phones, at, c, call_tool, two_shops, word  # noqa: F401  (fixtures and helpers)


def rest_line(product, color, quantity=1):
    return {"product_id": product.id, "variant_id": variant(product, color).id, "quantity": quantity}


def buyer(**extra):
    return {"buyer_name": "Dewi", "buyer_phone": str(next(_phones)), "address_line": "Jl. Dago 10", "city": "Bandung",
            "postal_code": "40135", **extra}


def available(product, color):
    return app_ctx.store.get_product(product.id).variants[[v.id for v in app_ctx.store.get_product(product.id).variants]
                                                          .index(variant(product, color).id)].stock.available


# ---------------------------------------------------------------- REST
def test_rest_checkout_takes_a_cart_and_makes_one_order(c, two_shops):
    (a, pa), _ = two_shops
    body = buyer(items=[rest_line(pa, "Black"), rest_line(pa, "Navy", 2)])
    order = c.post("/agent/v1/checkouts", json=body, headers=at(a.slug)).json()
    assert order["checkout_id"], order
    assert [i["qty"] for i in order["items"]] == [1, 2]
    assert order["subtotal"] == sum(i["unit_price"] * i["qty"] for i in order["items"])
    assert order["total"] == order["subtotal"] + order["shipping"]["price"]
    assert c.get(f"/agent/v1/checkouts/{order['checkout_id']}", headers=at(a.slug)).json()["checkout_id"] == order["checkout_id"]


def test_rest_checkout_still_takes_one_product_the_old_way(c, two_shops):
    (a, pa), _ = two_shops
    body = buyer(product_id=pa.id, variant_id=variant(pa, "Black").id, quantity=2)
    order = c.post("/agent/v1/checkouts", json=body, headers=at(a.slug)).json()
    assert [i["qty"] for i in order["items"]] == [2]


def test_rest_checkout_wants_one_product_or_a_cart_not_neither_not_both(c, two_shops):
    (a, pa), _ = two_shops
    assert c.post("/agent/v1/checkouts", json=buyer(), headers=at(a.slug)).status_code == 422
    both = buyer(product_id=pa.id, variant_id=variant(pa, "Black").id, items=[rest_line(pa, "Navy")])
    assert c.post("/agent/v1/checkouts", json=both, headers=at(a.slug)).status_code == 422
    assert c.post("/agent/v1/checkouts", json=buyer(items=[]), headers=at(a.slug)).status_code == 422
    too_many = buyer(items=[rest_line(pa, "Black")] * 11)
    assert c.post("/agent/v1/checkouts", json=too_many, headers=at(a.slug)).status_code == 422


def test_rest_cart_is_one_shop_and_checked_line_by_line(c, two_shops):
    (a, pa), (b, pb) = two_shops
    mixed = buyer(items=[rest_line(pa, "Black"), rest_line(pb, "Pink")])
    assert "same shop" in c.post("/agent/v1/checkouts", json=mixed, headers=at()).json()["error"]
    assert c.post("/agent/v1/checkouts", json=mixed, headers=at(a.slug)).json() == {"error": "product not found"}
    assert c.post("/agent/v1/checkouts", json=buyer(items=[rest_line(pb, "Pink")]), headers=at(a.slug)).json() == {"error": "product not found"}


def test_rest_cart_reserves_everything_or_nothing(c, two_shops):
    _, (b, pb) = two_shops
    pink_before = available(pb, "Pink")
    body = buyer(items=[rest_line(pb, "Pink"), rest_line(pb, "Cream", 3)])          # only 2 Cream exist
    assert "not enough stock" in c.post("/agent/v1/checkouts", json=body, headers=at(b.slug)).json()["error"]
    assert available(pb, "Pink") == pink_before                                      # the first line was not kept


def test_rest_cart_shipping_quote_covers_the_whole_cart(c, two_shops):
    (a, pa), (b, pb) = two_shops
    one = c.post("/agent/v1/shipping-options", json={"items": [rest_line(pa, "Black")], "postal_code": "40135"}, headers=at(a.slug)).json()
    many = c.post("/agent/v1/shipping-options", json={"items": [rest_line(pa, "Black", 3), rest_line(pa, "Navy", 3)], "postal_code": "40135"},
                  headers=at(a.slug)).json()
    assert one["options"] and many["options"]
    assert min(o["price"] for o in many["options"]) >= min(o["price"] for o in one["options"])    # more weight never costs less
    other = c.post("/agent/v1/shipping-options", json={"items": [rest_line(pb, "Pink")], "postal_code": "40135"}, headers=at(a.slug)).json()
    assert other == {"error": "product not found"}


# ---------------------------------------------------------------- MCP
def test_mcp_tools_take_a_cart(two_shops):
    (a, pa), (b, pb) = two_shops
    on_a = ShopScope(a.id, f"http://{a.slug}.{DOMAIN}")
    cart = [rest_line(pa, "Black"), rest_line(pa, "Navy", 2)]
    quote = call_tool("get_shipping_options", on_a, postal_code="40135", items=cart)
    assert quote["options"]
    order = call_tool("create_checkout", on_a, items=cart, **buyer())
    assert order["checkout_id"] and [i["qty"] for i in order["items"]] == [1, 2]
    foreign = [rest_line(pa, "Black"), rest_line(pb, "Pink")]
    assert call_tool("create_checkout", on_a, items=foreign, **buyer()) == {"error": "product not found"}
    assert call_tool("get_shipping_options", on_a, postal_code="40135", items=foreign) == {"error": "product not found"}
    assert "same shop" in call_tool("create_checkout", None, items=foreign, **buyer())["error"]


def test_mcp_checkout_schema_still_requires_the_buyer_and_offers_a_cart():
    schema = mcp._tool_manager._tools["create_checkout"].parameters
    assert {"buyer_name", "buyer_phone", "address_line", "city", "postal_code"} <= set(schema["required"])
    assert "items" in schema["properties"] and "items" not in schema["required"]
    quote = mcp._tool_manager._tools["get_shipping_options"].parameters
    assert quote["required"] == ["postal_code"] and "items" in quote["properties"]


# ---------------------------------------------------------------- what a two-item order does after checkout
def test_a_two_item_order_runs_the_whole_life_cycle(ctx, seller, product):
    """The seller's chat, the stock, the return window and the payout all treat the order as one with two lines."""
    from datetime import timedelta

    from etalase.models import OrderStatus, now

    from .conftest import BUYER, after_window

    black, navy = variant(product, "Black"), variant(product, "Navy")
    order = ctx.checkout.create_checkout(buyer=BUYER, items=[
        {"product_id": product.id, "variant_id": black.id, "qty": 2}, {"product_id": product.id, "variant_id": navy.id, "qty": 1}])
    ctx.checkout.payment_succeeded(order.payment.reference)

    after = ctx.store.get_product(product.id)
    assert after.variant(black.id).stock.on_hand == 3 and after.variant(navy.id).stock.on_hand == 2      # 5-2 and 3-1
    said = "\n".join(m.text for m in ctx.messenger.outbox)
    assert "× 2" in said and "× 1" in said and said.count(product.title) >= 2                            # both lines reach the seller

    ctx.checkout.seller_accepts(seller.id, order.id)
    ctx.checkout.mark_shipped(seller.id, None, "JNE777")
    delivered = ctx.checkout.shipment_update("JNE777", "delivered")
    assert delivered.status == OrderStatus.DELIVERED
    after_window(ctx)
    done = ctx.store.get_order(order.id)
    assert done.status == OrderStatus.PAID_OUT and done.payout.status == "released"
    commission = round(order.subtotal * ctx.settings.commission_rate)
    assert done.payout.commission == commission and done.payout.seller_amount == order.subtotal - commission   # on both lines


def test_an_unpaid_two_item_order_gives_back_both_reservations(ctx, seller, product):
    from datetime import timedelta

    from etalase.models import now

    from .conftest import BUYER

    black, navy = variant(product, "Black"), variant(product, "Navy")
    order = ctx.checkout.create_checkout(buyer=BUYER, items=[
        {"product_id": product.id, "variant_id": black.id, "qty": 2}, {"product_id": product.id, "variant_id": navy.id, "qty": 1}])
    held = ctx.store.get_product(product.id)
    assert held.variant(black.id).stock.reserved == 2 and held.variant(navy.id).stock.reserved == 1
    assert ctx.checkout.expire_due(now() + timedelta(minutes=16)) == [order.id]
    freed = ctx.store.get_product(product.id)
    assert freed.variant(black.id).stock.reserved == 0 and freed.variant(navy.id).stock.reserved == 0
