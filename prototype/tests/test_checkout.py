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


def test_reconciliation_of_stuck_releasing_payout(ctx, seller, product):
    """If a previous release attempt crashed mid-payout leaving status='releasing', the next sweep reconciles it."""
    from etalase.models import OrderStatus, Payout, now

    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    pay(ctx, order)
    ctx.checkout.seller_accepts(seller.id, order.id)
    ctx.checkout.mark_shipped(seller.id, order.id, "JNE123456")
    ctx.checkout.shipment_update("JNE123456", "delivered")

    # Simulate crash mid-payout: status is DELIVERED, but payout is stuck at "releasing"
    past = now()
    def simulate_crash(o):
        o.payout_due_at = past
        o.payout = Payout(seller_amount=81600, commission=3400, status="releasing")
        return True

    ctx.store.update_order(order.id, simulate_crash)
    stuck = ctx.store.get_order(order.id)
    assert stuck.status == OrderStatus.DELIVERED
    assert stuck.payout.status == "releasing"

    # Sweep should pick it up and successfully finish it
    released = ctx.checkout.release_due_payouts(when=past)
    assert order.id in released

    reconciled = ctx.store.get_order(order.id)
    assert reconciled.status == OrderStatus.PAID_OUT
    assert reconciled.payout.status == "released"


def test_multi_item_cart_checkout(ctx, seller, product):
    """Buyers/agents can purchase multiple items from the same shop in one checkout."""
    black = variant(product, "Black")
    navy = variant(product, "Navy")

    # Purchase 2 Black and 1 Navy together
    cart = [
        {"product_id": product.id, "variant_id": black.id, "qty": 2},
        {"product_id": product.id, "variant_id": navy.id, "qty": 1},
    ]
    order = ctx.checkout.create_checkout(buyer=BUYER, items=cart)
    assert len(order.items) == 2
    assert order.items[0].qty == 2 and order.items[0].variant_id == black.id
    assert order.items[1].qty == 1 and order.items[1].variant_id == navy.id
    assert order.subtotal == (2 * 85000) + (1 * 85000)
    assert order.status == OrderStatus.AWAITING_PAYMENT

    # Stock is reserved for both items
    assert ctx.store.get_product(product.id).variant(black.id).stock.reserved >= 2
    assert ctx.store.get_product(product.id).variant(navy.id).stock.reserved >= 1


def test_multi_item_atomic_rollback_on_out_of_stock(ctx, seller, product):
    """If one item in a cart is out of stock, all reservations in that checkout are rolled back cleanly."""
    black = variant(product, "Black")
    navy = variant(product, "Navy")

    # Black has plenty of stock (5), Navy has only 3
    init_black_reserved = ctx.store.get_product(product.id).variant(black.id).stock.reserved
    init_navy_reserved = ctx.store.get_product(product.id).variant(navy.id).stock.reserved

    cart = [
        (product.id, black.id, 2),
        (product.id, navy.id, 5),  # 5 exceeds Navy stock (3), but within 10 units limit
    ]
    with pytest.raises(CheckoutError, match="not enough stock"):
        ctx.checkout.create_checkout(buyer=BUYER, items=cart)

    # Black reservation MUST be rolled back: no lingering holds!
    assert ctx.store.get_product(product.id).variant(black.id).stock.reserved == init_black_reserved
    assert ctx.store.get_product(product.id).variant(navy.id).stock.reserved == init_navy_reserved


def test_multi_item_cross_seller_rejected(ctx, seller, product):
    """Items in a single checkout must belong to the same shop."""
    other = make_seller(ctx, name="Toko Budi", wa="6281999999999", kyc="verified")
    drafts, _ = ctx.catalog.ingest(other.id, ["Pashmina ceruti, coksu 5, harga 90rb"])
    ctx.catalog.publish(other.id)
    other_prod = drafts[0]

    cart = [
        (product.id, product.variants[0].id, 1),
        (other_prod.id, other_prod.variants[0].id, 1),
    ]
    with pytest.raises(CheckoutError, match="same shop"):
        ctx.checkout.create_checkout(buyer=BUYER, items=cart)


def test_multi_item_validation_error_messages(ctx, seller, product):
    """Ensure all multi-item cart edge cases produce clear, actionable CheckoutError messages."""
    black = variant(product, "Black")

    # 1. Empty items list
    with pytest.raises(CheckoutError, match="at least one item required"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[])

    # 2. Invalid item specification type
    with pytest.raises(CheckoutError, match="invalid item specification"):
        ctx.checkout.create_checkout(buyer=BUYER, items=["invalid_string"])

    # 3. Tuple too short
    with pytest.raises(CheckoutError, match="tuple must contain at least"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[(product.id,)])

    # 4. Missing product_id / variant_id in dict
    with pytest.raises(CheckoutError, match="product_id and variant_id are required"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[{"product_id": "", "variant_id": "var_123"}])

    # 5. Non-positive or invalid quantity
    with pytest.raises(CheckoutError, match="quantity must be at least 1"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[{"product_id": product.id, "variant_id": black.id, "qty": 0}])

    # 6. Variant not found produces clean error message, not raw KeyError
    with pytest.raises(CheckoutError, match="variant 'var_nonexistent' not found for product 'Voal Hijab'"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[(product.id, "var_nonexistent", 1)])

    # 7. Multi-item OutOfStock specifies exactly which item and variant ran out
    navy = variant(product, "Navy")
    with pytest.raises(CheckoutError, match=r"not enough stock for 'Voal Hijab \(Navy\)': requested 5, only 3 left"):
        ctx.checkout.create_checkout(buyer=BUYER, items=[
            (product.id, black.id, 1),
            (product.id, navy.id, 5),
        ])



