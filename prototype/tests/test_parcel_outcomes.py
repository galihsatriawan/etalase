"""What happens to the money when a parcel does not simply arrive.

  delivered   starts the return window; the payout follows when it ends          (covered in test_returns.py)
  returning   the courier is bringing the parcel back: nothing moves yet
  returned    the parcel is back with the seller: refund, restock
  lost        the courier reports it destroyed or disposed of: refund, nothing to restock
  failed      a booking problem: flagged for an operator, no money moves

Money moves only after the courier's own tracking record agrees with the webhook, and a webhook that was lost or came too
early is caught up by the periodic recheck.
"""

from datetime import timedelta

import httpx

from etalase.adapters.shipping import BiteshipShipping
from etalase.models import OrderStatus, Shipment, now

from .conftest import BUYER, variant
from .test_biteship import Resp
from .test_returns import events, outbox_to, shipped


def stock(ctx, product):
    return ctx.store.get_product(product.id).variant(variant(product, "Black").id).stock.on_hand


# ---------------------------------------------------------------- on its way back
def test_a_parcel_on_its_way_back_moves_no_money_and_tells_the_buyer_the_truth(ctx, seller, product):
    order = shipped(ctx, seller, product, "JNE700001")
    back = ctx.checkout.shipment_update("JNE700001", "returning")
    assert back.status == OrderStatus.SHIPPED and back.shipment.status == "returning"
    assert back.payout is None and back.payment.status == "paid" and ctx.payment.refunds == []
    assert any("bringing the parcel back" in line for line in back.history)
    assert "Do not tell the buyer it is on its way" in ctx.gateway.get_checkout_status(order.id)["next_step"]
    # a stale "in transit" that arrives after "on its way back" cannot undo it
    assert ctx.checkout.shipment_update("JNE700001", "in_transit").shipment.status == "returning"
    # the parcel never pays the seller while it is coming back, however long we wait
    assert ctx.process_due(now() + timedelta(days=60))["payouts_released"] == []
    assert ctx.store.get_order(order.id).payout is None


def test_the_order_page_says_the_parcel_is_coming_back_not_on_its_way():
    from fastapi.testclient import TestClient
    from etalase.api.main import app, ctx as app_ctx
    web = TestClient(app)
    s = web.post("/dev/sellers", json={"name": "Page Shop", "whatsapp": "6287700000077"}).json()
    drafts, _ = app_ctx.catalog.ingest(s["id"], ["Voal hijab, black 5, price 85k"], ["samples/voal-hijab.png"])
    app_ctx.catalog.publish(s["id"])
    p = app_ctx.store.get_product(drafts[0].id)
    o = app_ctx.checkout.create_checkout(p.id, p.variants[0].id, 1, BUYER)
    app_ctx.checkout.payment_succeeded(o.payment.reference)
    app_ctx.checkout.seller_accepts(s["id"], o.id)
    app_ctx.checkout.mark_shipped(s["id"], o.id, "JNE700002")
    assert "On its way" in web.get(f"/pay/{o.id}").text
    web.post("/webhooks/shipping", json={"waybill": "JNE700002", "status": "returning"})
    page = web.get(f"/pay/{o.id}").text
    assert "Coming back to the seller" in page and "On its way" not in page
    web.post("/webhooks/shipping", json={"waybill": "JNE700002", "status": "lost"})
    page = web.get(f"/pay/{o.id}").text
    assert "The courier lost the parcel" in page and "Coming back" not in page


def test_a_return_to_sender_refunds_in_full_and_restocks_once(ctx, seller, product):
    before = stock(ctx, product)
    order = shipped(ctx, seller, product, "JNE700003")
    assert stock(ctx, product) == before - 1
    ctx.checkout.shipment_update("JNE700003", "returning")
    done = ctx.checkout.shipment_update("JNE700003", "returned")
    assert done.status == OrderStatus.REFUNDED and done.payment.status == "refunded" and done.payment.refunded_amount == order.total
    assert stock(ctx, product) == before
    ctx.checkout.shipment_update("JNE700003", "returned")                     # a repeated event changes nothing
    assert ctx.payment.refunds == [order.id] and stock(ctx, product) == before
    assert ctx.process_due(now() + timedelta(days=60))["payouts_released"] == []
    assert "refunded in full" in ctx.gateway.get_checkout_status(order.id)["next_step"]


# ---------------------------------------------------------------- lost
def test_a_parcel_the_courier_lost_refunds_the_buyer_and_never_pays_the_seller(ctx, seller, product):
    before = stock(ctx, product)
    order = shipped(ctx, seller, product, "JNE700004")
    done = ctx.checkout.shipment_update("JNE700004", "lost")
    assert done.status == OrderStatus.REFUNDED and done.shipment.status == "lost"
    assert done.payment.status == "refunded" and done.payment.refunded_amount == order.total and done.payout is None
    assert stock(ctx, product) == before - 1                                  # the goods are gone: nothing goes back on the shelf
    assert events(ctx, "order.parcel_lost") == [{"order_id": order.id}]
    assert "lost or destroyed" in outbox_to(ctx, seller.whatsapp)[-1].text
    assert "courier lost the parcel" in ctx.gateway.get_checkout_status(order.id)["next_step"]
    # nothing can pay or refund this order a second time
    for late in ("lost", "returned", "delivered", "in_transit"):
        ctx.checkout.shipment_update("JNE700004", late)
    assert ctx.payment.refunds == [order.id] and ctx.payment.releases == []
    assert ctx.store.get_order(order.id).status == OrderStatus.REFUNDED
    assert ctx.process_due(now() + timedelta(days=60))["payouts_released"] == []


def test_a_lost_parcel_is_checked_with_the_courier_before_the_buyer_is_refunded(ctx, seller, product, monkeypatch):
    order = shipped(ctx, seller, product, "JNE700005")
    monkeypatch.setattr(ctx.shipping, "confirm_lost", lambda o: False)       # a forged event, or tracking that does not agree
    still = ctx.checkout.shipment_update("JNE700005", "lost")
    assert still.status == OrderStatus.SHIPPED and still.shipment.status == "picked_up"      # the event changed nothing
    assert ctx.payment.refunds == []
    monkeypatch.setattr(ctx.shipping, "confirm_lost", lambda o: True)
    assert ctx.checkout.shipment_update("JNE700005", "lost").status == OrderStatus.REFUNDED
    assert ctx.payment.refunds == [order.id]


def test_a_refund_the_provider_cannot_do_yet_stays_visible_for_a_lost_parcel(ctx, seller, product, monkeypatch):
    from etalase.adapters.payment import ManualActionRequired
    order = shipped(ctx, seller, product, "JNE700006")

    def cannot(order, amount):
        raise ManualActionRequired("send it by hand")
    monkeypatch.setattr(ctx.payment, "refund", cannot)
    done = ctx.checkout.shipment_update("JNE700006", "lost")
    assert done.status == OrderStatus.REFUNDED and done.payment.status == "refund_pending"
    assert {"order_id": order.id, "action": "refund"} in events(ctx, "ops.manual_action")


# ---------------------------------------------------------------- failed bookings stay a human matter
def test_a_booking_problem_flags_an_operator_and_moves_no_money(ctx, seller, product):
    order = shipped(ctx, seller, product, "JNE700007")
    out = ctx.checkout.shipment_update("JNE700007", "failed")
    assert out.status == OrderStatus.SHIPPED and out.payout is None and ctx.payment.refunds == []
    assert events(ctx, "ops.manual_action").count({"order_id": order.id, "action": "undelivered_parcel"}) == 1
    assert "operator has been alerted" in ctx.gateway.get_checkout_status(order.id)["next_step"]


# ---------------------------------------------------------------- catching up on missed courier events
def test_a_missed_webhook_is_caught_up_by_the_recheck(ctx, seller, product, monkeypatch):
    order = shipped(ctx, seller, product, "JNE700008")
    monkeypatch.setattr(ctx.shipping, "tracking_status", lambda o: "lost")
    assert ctx.checkout.reconcile_shipments(now() + timedelta(hours=1)) == []                   # too soon: not asked yet
    assert ctx.checkout.reconcile_shipments(now() + timedelta(hours=7)) == [order.id]
    assert ctx.store.get_order(order.id).status == OrderStatus.REFUNDED


def test_the_recheck_asks_each_parcel_once_per_interval_and_ignores_what_is_not_final(ctx, seller, product, monkeypatch):
    shipped(ctx, seller, product, "JNE700009")
    asked = []
    monkeypatch.setattr(ctx.shipping, "tracking_status", lambda o: asked.append(o.id) or "returning")
    at = now() + timedelta(hours=7)
    assert ctx.checkout.reconcile_shipments(at) == [] and len(asked) == 1
    assert ctx.checkout.reconcile_shipments(at + timedelta(minutes=1)) == [] and len(asked) == 1     # not again every minute
    assert ctx.checkout.reconcile_shipments(at + timedelta(hours=7)) == [] and len(asked) == 2


def test_a_courier_that_errors_during_the_recheck_changes_nothing_and_is_asked_again_later(ctx, seller, product, monkeypatch):
    order = shipped(ctx, seller, product, "JNE700010")

    def boom(o):
        raise RuntimeError("courier down")
    monkeypatch.setattr(ctx.shipping, "tracking_status", boom)
    assert ctx.checkout.reconcile_shipments(now() + timedelta(hours=7)) == []
    assert ctx.store.get_order(order.id).status == OrderStatus.SHIPPED


def test_the_background_job_runs_the_recheck(ctx, seller, product, monkeypatch):
    order = shipped(ctx, seller, product, "JNE700011")
    monkeypatch.setattr(ctx.shipping, "tracking_status", lambda o: "delivered")
    out = ctx.process_due(now() + timedelta(hours=7))
    assert out["shipments_reconciled"] == [order.id]
    assert ctx.store.get_order(order.id).status == OrderStatus.DELIVERED


# ---------------------------------------------------------------- the Biteship side
def test_biteship_reads_disposed_from_the_tracking_record_and_maps_it_to_lost(monkeypatch):
    from .test_biteship import order_for_booking
    shipping = BiteshipShipping("k")
    parcel = order_for_booking()
    parcel.shipment = Shipment(courier="jne", service="reg", waybill="W1", tracking_id="trk1", provider_order_id="oid1")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(200, {"status": "disposed"}))
    assert shipping.confirm_lost(parcel) and not shipping.confirm_returned(parcel) and not shipping.confirm_delivered(parcel)
    assert shipping.tracking_status(parcel) == "lost"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(200, {"status": "return_in_transit"}))
    assert not shipping.confirm_lost(parcel) and shipping.tracking_status(parcel) == "returning"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(500))
    assert not shipping.confirm_lost(parcel) and shipping.tracking_status(parcel) == ""
