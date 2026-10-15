"""Regression tests for issues found while writing the Etalase Academy guide."""

from etalase.models import OrderStatus, StockOwner
from etalase.seller.inbox import SellerInbox

from .conftest import BUYER, make_product, make_seller, variant


class RetryingStore:
    """Wraps MemoryStore and runs every update_order callback twice, like a Firestore transaction retry."""

    def __init__(self, inner, stale=None):
        self._inner = inner
        self._stale = stale or {}  # order_id -> an older copy the first attempt reads

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def update_order(self, order_id, change):
        snapshot = self._stale.pop(order_id, None) or self._inner.get_order(order_id)
        if snapshot is not None:
            change(snapshot.model_copy(deep=True))  # first attempt, discarded as if it conflicted
        return self._inner.update_order(order_id, change)


def test_payment_callbacks_survive_transaction_retries(ctx, seller):
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Mocca").id, 1, BUYER)
    ctx.checkout.store = RetryingStore(ctx.store)
    paid = ctx.checkout.payment_succeeded(order.payment.reference)
    assert paid.status == OrderStatus.PAID
    assert [t for t, p in ctx.events.published if t == "order.paid" and p["order_id"] == order.id]


def test_late_payment_is_refunded_even_with_retries(ctx, seller):
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Mocca").id, 1, BUYER)
    before_expiry = ctx.store.get_order(order.id)
    ctx.checkout.expire_due(order.expires_at)
    # First attempt reads the order while it was still awaiting payment, the retry sees it expired.
    ctx.checkout.store = RetryingStore(ctx.store, {order.id: before_expiry})
    late = ctx.checkout.payment_succeeded(order.payment.reference)
    assert late.status == OrderStatus.EXPIRED
    assert late.payment.status == "refunded"
    assert not [t for t, p in ctx.events.published if t == "order.paid" and p["order_id"] == order.id]


def test_session_tokens_and_oauth_state_are_not_interchangeable(ctx, seller):
    state = ctx.auth.sign(f"ig.{seller.id}.9999999999")
    assert ctx.auth.seller_from_token(state) is None
    assert ctx.auth.unsign(ctx.auth.issue(seller.id)) is None
    assert ctx.auth.seller_from_token(ctx.auth.issue(seller.id)) == seller.id
    assert ctx.auth.seller_from_token("tök.123.äbc") is None  # non-ASCII must not crash


def test_vague_stock_message_asks_which_product(ctx, seller):
    make_product(ctx, seller, "Voal hijab, black 5, navy 3, price 85k")
    make_product(ctx, seller, "Pashmina silk, black 4, price 120k")
    inbox: SellerInbox = ctx.inbox
    inbox.handle_intent(seller.id, "black sold out")
    assert "Which product?" in ctx.messenger.outbox[-1].text
    stocks = [v.stock.on_hand for p in ctx.store.list_products(seller_id=seller.id) for v in p.variants
              if v.options.get("color") == "Black"]
    assert sorted(stocks) == [4, 5]  # nothing changed
    inbox.handle_intent(seller.id, "pashmina black sold out")
    stocks = {p.title: v.stock.on_hand for p in ctx.store.list_products(seller_id=seller.id) for v in p.variants
              if v.options.get("color") == "Black"}
    assert sorted(stocks.values()) == [0, 5]


def test_late_available_tap_does_not_claim_payment_was_sent(ctx, seller):
    product = make_product(ctx, seller, "Voal hijab, black 1, price 85k")  # 1 left -> confirm first
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    assert order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION
    ctx.checkout.expire_due(order.expires_at)
    ctx.inbox.handle_button(seller.id, f"confirm:{order.id}")
    assert "already expired" in ctx.messenger.outbox[-1].text


def test_rejection_does_not_touch_stock_owned_by_an_oms(ctx):
    seller = make_seller(ctx, wa="6281111111111")
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Mocca").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    s = ctx.store.get_seller(seller.id)
    s.stock_owner = StockOwner.OMS
    ctx.store.put_seller(s)
    ctx.checkout.seller_rejects_paid(seller.id, order.id)
    assert variant(ctx.store.get_product(product.id), "Mocca").stock.on_hand == 10  # restocked, not zeroed


def test_short_titles_can_be_published_and_plural_search_works(ctx, seller):
    drafts, _ = ctx.catalog.ingest(seller.id, ["Tas, black 3, price 150k"], [])
    assert drafts and "title" not in drafts[0].readiness_missing
    make_product(ctx, seller)
    assert ctx.catalog.search("black hijabs")


def test_shipment_status_never_goes_backwards(ctx, seller):
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Mocca").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    shipped = ctx.checkout.mark_shipped(seller.id, order.id, "JNE1234567")
    ctx.checkout.shipment_update(shipped.shipment.waybill, "delivered")
    after = ctx.checkout.shipment_update(shipped.shipment.waybill, "in_transit")
    assert after.shipment.status == "delivered"
    assert after.status == OrderStatus.PAID_OUT


def test_unknown_courier_is_an_error_not_a_silent_fallback(ctx, seller):
    import pytest

    from etalase.models import CheckoutError
    product = make_product(ctx, seller)
    with pytest.raises(CheckoutError, match="not available"):
        ctx.checkout.create_checkout(product.id, variant(product, "Mocca").id, 1, BUYER, courier="pigeon")
