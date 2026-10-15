"""Xendit contract as far as the docs allow (help.xendit.co, docs.xendit.co, checked 2026-10-15). HTTP is faked, so this
proves what we send and how we read callbacks; only a real test key and the Xendit simulator prove Xendit accepts it."""

from datetime import datetime, timezone

import httpx
import pytest

from etalase.adapters.payment import XenditPayment
from etalase.models import CheckoutError

from .conftest import BUYER, make_product, make_seller, variant
from .test_biteship import Resp, order_for_booking

TOKEN = "cb-token-123456"


def xendit():
    return XenditPayment("xnd_development_SECRET", TOKEN)


def test_create_qr_request_matches_the_documented_shape(monkeypatch):
    sent = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(url=url, **kw) or Resp(200, {
        "id": "qr_1d2a", "reference_id": "ord_x", "type": "DYNAMIC", "currency": "IDR", "amount": 182000,
        "channel_code": "ID_DANA", "status": "ACTIVE", "qr_string": "00020101021226...", "expires_at": "2026-10-15T12:15:00.000000Z"}))
    order = order_for_booking()
    order.expires_at = datetime(2026, 10, 15, 12, 15, 0, 123456, tzinfo=timezone.utc)
    info = xendit().create_payment(order)
    assert sent["url"] == "https://api.xendit.co/qr_codes"
    assert sent["headers"]["api-version"] == "2022-07-31" and sent["headers"]["Authorization"].startswith("Basic ")
    assert "for-user-id" not in sent["headers"]
    assert sent["json"] == {"reference_id": order.id, "type": "DYNAMIC", "currency": "IDR", "amount": 182000,
                            "expires_at": "2026-10-15T12:15:00.123456Z"}       # UTC with a trailing Z, like Xendit's own examples
    assert (info.reference, info.qr_string, info.provider_id) == (order.id, "00020101021226...", "qr_1d2a")


def test_basic_auth_is_the_secret_key_with_an_empty_password(monkeypatch):
    import base64
    assert base64.b64decode(xendit().headers["Authorization"].split()[1]).decode() == "xnd_development_SECRET:"


def test_sub_account_header_is_sent_only_for_sellers_with_one(monkeypatch):
    sent = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(kw) or Resp(200, {"id": "q", "qr_string": "x"}))
    xendit().create_payment(order_for_booking(), "acc_123")
    assert sent["headers"]["for-user-id"] == "acc_123"


def test_a_failed_creation_raises_after_logging_the_body(monkeypatch, caplog):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(400, {"error_code": "API_VALIDATION_ERROR", "message": "bad expires_at"}))
    with caplog.at_level("ERROR"), pytest.raises(httpx.HTTPStatusError):
        xendit().create_payment(order_for_booking())
    assert "API_VALIDATION_ERROR" in caplog.text


PAID = {"event": "qr.payment", "created": "2026-10-15T12:03:00.000Z", "business_id": "b1", "api_version": "2022-07-31",
        "data": {"id": "qrpy_9", "qr_id": "qr_1d2a", "reference_id": "ord_abc", "status": "SUCCEEDED", "amount": 182000}}


def test_a_paid_callback_with_the_right_token_is_a_payment():
    assert xendit().parse_webhook({"x-callback-token": TOKEN}, PAID) == ("ord_abc", "paid")


def test_wrong_or_missing_callback_token_is_rejected():
    for headers in ({}, {"x-callback-token": "nope"}):
        with pytest.raises(PermissionError):
            xendit().parse_webhook(headers, PAID)


def test_refund_callbacks_and_other_events_never_count_as_payments():
    refund = {**PAID, "event": "qr.refund"}
    assert xendit().parse_webhook({"x-callback-token": TOKEN}, refund) is None
    pending = {**PAID, "data": {**PAID["data"], "status": "PENDING"}}
    assert xendit().parse_webhook({"x-callback-token": TOKEN}, pending) is None


def test_the_older_flat_callback_shape_still_works():
    flat = {"reference_id": "ord_abc", "status": "COMPLETED"}
    assert xendit().parse_webhook({"x-callback-token": TOKEN}, flat) == ("ord_abc", "paid")


def test_orders_above_the_qris_limit_are_refused_before_stock_is_held(ctx, monkeypatch):
    seller = make_seller(ctx)
    product = make_product(ctx, seller)
    monkeypatch.setattr(ctx.payment, "max_amount", 150_000, raising=False)
    v = variant(product, "Black")
    with pytest.raises(CheckoutError, match="QRIS"):
        ctx.checkout.create_checkout(product.id, v.id, 2, BUYER)          # 2 x 85,000 + shipping > 150,000
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == 0
