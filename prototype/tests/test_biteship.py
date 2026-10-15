"""Biteship contract, pinned from the official docs (biteship.com/docs/api, checked 2026-10-15).
The shapes below are what the docs show; the HTTP layer is faked, so these tests prove we send and read the documented
fields, not that Biteship accepts them (that needs a real key: `make doctor`)."""

import httpx
import pytest

from etalase.adapters.shipping import BiteshipShipping
from etalase.models import Address, Order, OrderItem, OrderStatus, SellMode, Shipment, ShippingRate
from etalase.models import CheckoutError

from .conftest import BUYER, make_product, make_seller, variant


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("GET", "http://x"), response=self)


def order_for_booking():
    return Order(seller_id="sel_1", sell_mode=SellMode.INSTANT, status=OrderStatus.ACCEPTED, subtotal=170000, total=182000,
                 buyer=Address(name="Dewi", phone="6285700001111", line1="Jl. Dago 10", city="Bandung", postal_code="40135"),
                 items=[OrderItem(product_id="p", variant_id="v", sku="S", title="Hijab Voal (Black)", qty=2,
                                  unit_price=85000, weight_gram=150)],
                 shipping_rate=ShippingRate(courier="jne", service="reg", price=12000, etd="2 - 3 days"))


def test_rates_request_and_response_follow_the_docs(monkeypatch):
    sent = {}

    def post(url, **kw):
        sent.update(url=url, **kw)
        return Resp(200, {"success": True, "pricing": [
            {"courier_code": "jne", "courier_service_code": "reg", "price": 24000, "duration": "2 - 3 days",
             "shipment_duration_range": "2 - 3", "shipment_duration_unit": "days"}]})
    monkeypatch.setattr(httpx, "post", post)
    rates = BiteshipShipping("biteship_test.abc").get_rates("12950", "40135", 300, 170000)
    assert sent["url"] == "https://api.biteship.com/v1/rates/couriers"
    assert sent["headers"]["authorization"] == "biteship_test.abc"          # raw key, no "Bearer", per the docs' curl example
    body = sent["json"]
    assert body["origin_postal_code"] == 12950 and body["destination_postal_code"] == 40135
    assert body["couriers"] == "jne,sicepat,jnt,anteraja"
    assert body["items"] == [{"name": "parcel", "value": 170000, "quantity": 1, "weight": 300}]   # weight in grams
    assert rates == [ShippingRate(courier="jne", service="reg", price=24000, etd="2 - 3 days")]


def test_booking_sends_the_origin_postal_code_and_a_positive_weight_and_keeps_biteships_ids(monkeypatch):
    sent = {}

    def post(url, **kw):
        sent.update(url=url, **kw)
        return Resp(200, {"success": True, "id": "5dd599ebdefcd4158eb8470b", "status": "confirmed",
                          "courier": {"waybill_id": "WYB-111", "tracking_id": "6de509ebdefgh4158ij3451c"}})
    monkeypatch.setattr(httpx, "post", post)
    shipment = BiteshipShipping("k").book(order_for_booking(), "Toko Rina", "6281234567890", "Jl. Contoh 1", "12950")
    body = sent["json"]
    assert sent["url"] == "https://api.biteship.com/v1/orders"
    assert body["origin_postal_code"] == 12950            # docs: origin needs a postal code, coordinate or area id
    assert body["destination_postal_code"] == 40135
    assert (body["courier_company"], body["courier_type"], body["delivery_type"]) == ("jne", "reg", "now")
    assert body["items"] == [{"name": "Hijab Voal (Black)", "value": 85000, "quantity": 2, "weight": 150}]
    assert body["reference_id"].startswith("ord_")      # our order id; a repeat is rejected as a duplicate
    assert (shipment.waybill, shipment.provider_order_id, shipment.tracking_id) == ("WYB-111", "5dd599ebdefcd4158eb8470b", "6de509ebdefgh4158ij3451c")


def test_booking_without_an_origin_postal_code_fails_before_calling_biteship(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call Biteship without an origin postal code"))
    with pytest.raises(ValueError):
        BiteshipShipping("k").book(order_for_booking(), "Toko", "62812", "Jl. X", "")


def test_service_codes_are_sent_exactly_as_the_rates_call_returned_them(monkeypatch):
    sent = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(kw) or Resp(200, {"id": "x", "courier": {}}))
    order = order_for_booking()
    order.shipping_rate = ShippingRate(courier="dash_express", service="SAME_DAY", price=1, etd="")
    BiteshipShipping("k").book(order, "T", "62812", "Jl. X", "12950")
    assert sent["json"]["courier_type"] == "SAME_DAY"


def test_a_booking_without_an_instant_waybill_still_keeps_ids_for_webhooks(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(200, {"id": "oid1", "courier": {"waybill_id": None, "tracking_id": "trk1"}}))
    s = BiteshipShipping("k").book(order_for_booking(), "T", "62812", "Jl. X", "12950")
    assert s.waybill == "" and s.provider_order_id == "oid1" and s.tracking_id == "trk1"


def test_errors_keep_biteships_message_and_code(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(400, {"success": False, "error": "Either origin needs to have postal code or coordinate", "code": 40002011}))
    with pytest.raises(RuntimeError, match="40002011"):
        BiteshipShipping("k").book(order_for_booking(), "T", "62812", "Jl. X", "12950")


@pytest.mark.parametrize("payload,expected", [
    ({"event": "order.status", "order_id": "oid1", "courier_waybill_id": "W1", "courier_tracking_id": "t1", "status": "delivered"}, ("oid1", "delivered")),
    ({"event": "order.status", "order_id": "oid1", "status": "picked"}, ("oid1", "picked_up")),
    ({"event": "order.status", "order_id": "oid1", "status": "dropping_off"}, ("oid1", "in_transit")),
    ({"event": "order.status", "order_id": "oid1", "status": "allocated"}, ("oid1", "booked")),
    ({"event": "order.status", "order_id": "oid1", "status": "return_in_transit"}, ("oid1", "returned")),
    ({"event": "order.status", "order_id": "oid1", "status": "courier_not_found"}, ("oid1", "failed")),
    ({"event": "order.status", "order_id": "oid1", "status": "cancelled"}, ("oid1", "failed")),
    ({"event": "order.status", "courier_waybill_id": "W1", "status": "delivered"}, ("W1", "delivered")),
    ({"event": "order.price", "order_id": "oid1", "price": 12000, "status": "confirmed"}, None),
    ({"event": "order.waybill_id", "order_id": "oid1", "courier_waybill_id": "W9", "status": "allocated"}, None),
    ({"event": "order.status", "order_id": "oid1", "status": "something_new"}, None),
])
def test_webhook_events(payload, expected):
    assert BiteshipShipping("k").parse_webhook(payload) == expected


def test_delivery_is_checked_by_tracking_id_for_booked_orders_and_by_waybill_for_hand_typed_ones(monkeypatch):
    urls = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: urls.append(url) or Resp(200, {"status": "delivered"}))
    shipping = BiteshipShipping("k")
    booked, typed = order_for_booking(), order_for_booking()
    booked.shipment = Shipment(courier="jne", service="reg", waybill="W1", tracking_id="trk1", provider_order_id="oid1")
    typed.shipment = Shipment(courier="jne", service="reg", waybill="JNE123456")
    assert shipping.confirm_delivered(booked) and shipping.confirm_delivered(typed)
    assert urls == ["https://api.biteship.com/v1/trackings/trk1", "https://api.biteship.com/v1/trackings/JNE123456/couriers/jne"]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(200, {"status": "on_hold"}))
    assert not shipping.confirm_delivered(booked)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(500))
    assert not shipping.confirm_delivered(booked)
    no_ids = order_for_booking(); no_ids.shipment = Shipment(courier="jne", service="reg")
    assert not shipping.confirm_delivered(no_ids)


def test_a_provider_webhook_finds_the_order_by_biteships_ids_and_releases_the_payout(ctx):
    seller = make_seller(ctx)
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    shipped = ctx.checkout.mark_shipped(seller.id, order.id, "JNE123456")
    ctx.store.update_order(order.id, lambda o: setattr(o.shipment, "provider_order_id", "oid1") or setattr(o.shipment, "tracking_id", "trk1") or True)
    assert shipped.status == OrderStatus.SHIPPED
    done = ctx.checkout.shipment_update("oid1", "delivered")      # the mock courier confirms delivery
    assert done.status in (OrderStatus.DELIVERED, OrderStatus.PAID_OUT)
    assert ctx.checkout.shipment_update("trk1", "delivered").id == order.id


def test_a_failed_parcel_is_logged_and_never_pays_out(ctx):
    seller = make_seller(ctx)
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    ctx.checkout.mark_shipped(seller.id, order.id, "JNE123456")
    result = ctx.checkout.shipment_update("JNE123456", "failed")
    assert result.status == OrderStatus.SHIPPED and result.shipment.status == "failed" and result.payout is None


def test_a_rates_outage_is_a_checkout_error_not_a_server_error(ctx, monkeypatch):
    seller = make_seller(ctx)
    product = make_product(ctx, seller)
    monkeypatch.setattr(ctx.shipping, "get_rates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Biteship HTTP 402: balance")))
    with pytest.raises(CheckoutError, match="unavailable"):
        ctx.checkout.shipping_options(product.id, variant(product, "Black").id, 1, "40135")
    with pytest.raises(CheckoutError, match="unavailable"):
        ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
