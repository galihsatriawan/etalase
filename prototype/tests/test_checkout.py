from datetime import timedelta

import pytest

from etalase.models import Address, CheckoutError, OrderStatus, now

from .conftest import BUYER, after_window, make_seller, variant


def pay(ctx, order):
    return ctx.checkout.payment_succeeded(order.payment.reference)


def test_instant_order_end_to_end(ctx, seller, product):
    black = variant(product, "Black")
    order = ctx.checkout.create_checkout(product.id, black.id, 2, BUYER)
    assert order.status == OrderStatus.AWAITING_PAYMENT and order.payment.qr_string
    assert order.total == 2 * 85000 + order.shipping_fee

    paid = pay(ctx, order)
    assert paid.status == OrderStatus.PAID
    assert pay(ctx, order).status == OrderStatus.PAID  # duplicate webhook is harmless
    assert ctx.store.get_product(product.id).variant(black.id).stock.on_hand == 3

    ctx.checkout.seller_accepts(seller.id, order.id)
    shipped = ctx.checkout.mark_shipped(seller.id, None, "JNE123456")
    assert shipped.status == OrderStatus.SHIPPED
    delivered = ctx.checkout.shipment_update("JNE123456", "delivered")
    assert delivered.status == OrderStatus.DELIVERED and delivered.payout is None   # the money waits out the return window
    assert ctx.payment.releases == []
    after_window(ctx)
    done = ctx.store.get_order(order.id)
    assert done.status == OrderStatus.PAID_OUT
    assert done.payout.commission == round(170000 * 0.04)
    assert done.payout.seller_amount == 170000 - done.payout.commission
    texts = [m.text for m in ctx.messenger.outbox]
    assert any("New paid order" in t for t in texts) and any("Parcel delivered" in t for t in texts)


def test_confirm_first_when_stock_is_low(ctx, seller, product):
    navy = variant(product, "Navy")
    ctx.catalog.update_stock(seller.id, "Navy", 2)
    order = ctx.checkout.create_checkout(product.id, navy.id, 1, BUYER)
    assert order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION and order.payment is None
    assert ctx.messenger.outbox[-1].buttons[0][0] == f"confirm:{order.id}"

    order = ctx.checkout.seller_confirms_stock(seller.id, order.id, available=True)
    assert order.status == OrderStatus.AWAITING_PAYMENT and order.payment
    assert pay(ctx, order).status == OrderStatus.ACCEPTED  # already confirmed by the seller


def test_seller_says_sold_out_releases_and_zeroes_stock(ctx, seller, product):
    navy = variant(product, "Navy")
    ctx.catalog.update_stock(seller.id, "Navy", 1)
    order = ctx.checkout.create_checkout(product.id, navy.id, 1, BUYER)
    order = ctx.checkout.seller_confirms_stock(seller.id, order.id, available=False)
    assert order.status == OrderStatus.REJECTED
    v = ctx.store.get_product(product.id).variant(navy.id)
    assert (v.stock.on_hand, v.stock.reserved) == (0, 0)


def test_reject_after_payment_refunds(ctx, seller, product):
    black = variant(product, "Black")
    order = ctx.checkout.create_checkout(product.id, black.id, 1, BUYER)
    pay(ctx, order)
    order = ctx.checkout.seller_rejects_paid(seller.id, order.id)
    assert order.status == OrderStatus.REFUNDED
    assert order.id in ctx.payment.refunds


def test_unpaid_checkout_expires_and_releases_stock(ctx, seller, product):
    black = variant(product, "Black")
    order = ctx.checkout.create_checkout(product.id, black.id, 5, BUYER)
    assert ctx.store.get_product(product.id).variant(black.id).stock.available == 0
    assert ctx.checkout.expire_due(now() + timedelta(minutes=16)) == [order.id]
    assert ctx.store.get_order(order.id).status == OrderStatus.EXPIRED
    assert ctx.store.get_product(product.id).variant(black.id).stock.available == 5


def test_domestic_only(ctx, seller, product):
    abroad = Address(name="Ann", line1="1 Orchard Rd", city="Singapore", postal_code="238801", country="SG")
    with pytest.raises(CheckoutError, match="domestic"):
        ctx.checkout.create_checkout(product.id, product.variants[0].id, 1, abroad)


def test_seller_without_kyc_cannot_sell(ctx):
    pending = make_seller(ctx, name="New Shop", wa="6281111111111", kyc="pending")
    drafts, _ = ctx.catalog.ingest(pending.id, ["Kaos polos, putih 9, harga 75rb"])
    ctx.catalog.publish(pending.id)
    with pytest.raises(CheckoutError, match="not accepting"):
        ctx.checkout.create_checkout(drafts[0].id, drafts[0].variants[0].id, 1, BUYER)


def test_tenant_isolation_on_orders(ctx, seller, product):
    other = make_seller(ctx, name="Other", wa="6289999999999")
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    pay(ctx, order)
    with pytest.raises(CheckoutError):
        ctx.checkout.seller_accepts(other.id, order.id)


def test_gateway_returns_errors_not_exceptions(ctx, seller, product):
    r = ctx.gateway.create_checkout(product.id, product.variants[0].id, 999, "Dewi", "1", "Jl", "Bandung", "40135")
    assert "error" in r
    found = ctx.gateway.search_products("black voal hijab", max_price_idr=100000)
    assert found["count"] == 1 and found["results"][0]["variants"][0]["options"]["color"] == "Black"
    assert ctx.gateway.search_products("voal hijab", max_price_idr=50000)["count"] == 0
