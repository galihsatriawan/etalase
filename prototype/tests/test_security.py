"""Security and correctness guards: config checks, OTP abuse, webhook trust, races, input limits."""

import asyncio
from datetime import timedelta

import pytest

from etalase import auth as auth_module
from etalase.adapters.messaging import ConsoleMessenger, Inbound
from etalase.adapters.payment import ManualActionRequired
from etalase.config import Settings
from etalase.connector.service import ProductIn, VariantIn, check_webhook_url
from etalase.models import Address, CheckoutError, OrderStatus, now
from etalase.security import InsecureConfig, validate_settings

from .conftest import BUYER, variant

LOCAL_MODES = {"payment": "mock", "shipping": "mock", "messaging": "console"}


def buyer(i: int) -> Address:
    return BUYER.model_copy(update={"phone": f"62857{i:08d}"})


# ---------------------------------------------------------------- configuration
def test_local_dev_starts_without_secrets():
    validate_settings(Settings(etalase_env="dev", _env_file=None), LOCAL_MODES)


def test_public_dev_deploy_requires_tokens():
    s = Settings(etalase_env="dev", public_base_url="https://etalase-xyz.a.run.app", _env_file=None)
    with pytest.raises(InsecureConfig, match="DEV_TOKEN"):
        validate_settings(s, LOCAL_MODES)
    ok = Settings(etalase_env="dev", public_base_url="https://etalase-xyz.a.run.app", dev_token="d" * 24,
                  webhook_token="w" * 24, session_secret="s" * 40, _env_file=None)
    validate_settings(ok, LOCAL_MODES)


def test_prod_refuses_mocks_and_default_secret():
    s = Settings(etalase_env="prod", public_base_url="https://etalase.example", _env_file=None)
    with pytest.raises(InsecureConfig) as e:
        validate_settings(s, LOCAL_MODES)
    msg = str(e.value)
    assert "SESSION_SECRET" in msg and "mock payment" in msg and "WhatsApp" in msg


def test_real_providers_need_their_webhook_secrets():
    s = Settings(etalase_env="dev", whatsapp_token="t", xendit_secret_key="k", _env_file=None)
    with pytest.raises(InsecureConfig) as e:
        validate_settings(s, LOCAL_MODES)
    assert "WHATSAPP_APP_SECRET" in str(e.value) and "XENDIT_CALLBACK_TOKEN" in str(e.value)


# ---------------------------------------------------------------- OTP sign-in
def test_otp_resend_cooldown(ctx, seller):
    ctx.auth.request(seller.whatsapp)
    ctx.auth.request(seller.whatsapp)  # within 60 s: silently ignored
    assert sum("code" in m.text.lower() for m in ctx.messenger.outbox) == 1


def test_otp_lockout_across_codes(ctx, seller, monkeypatch):
    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN", 0)
    monkeypatch.setattr(auth_module, "MAX_CODES_PER_HOUR", 100)
    for _ in range(auth_module.MAX_FAILURES_PER_HOUR):
        ctx.auth.request(seller.whatsapp)
        assert ctx.auth.verify(seller.whatsapp, "000000x") is None
    ctx.auth.request(seller.whatsapp)  # locked: no new code is sent
    sent = [m.text for m in ctx.messenger.outbox]
    code = sent[-1].split(": ")[1][:6]
    assert ctx.auth.verify(seller.whatsapp, code) is None


def test_otp_code_dies_after_max_attempts(ctx, seller):
    ctx.auth.request(seller.whatsapp)
    code = ctx.messenger.outbox[-1].text.split(": ")[1][:6]
    for _ in range(auth_module.MAX_ATTEMPTS):
        ctx.auth.verify(seller.whatsapp, "wrong!")
    assert ctx.auth.verify(seller.whatsapp, code) is None


def test_signed_state_round_trip(ctx):
    signed = ctx.auth.sign("ig.sel_1.123")
    assert ctx.auth.unsign(signed) == "ig.sel_1.123"
    assert ctx.auth.unsign(signed[:-1] + ("0" if signed[-1] != "0" else "1")) is None


# ---------------------------------------------------------------- payments and races
def test_mock_qr_does_not_leak_payment_reference(ctx, seller, product):
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    assert order.payment.reference not in order.payment.qr_string


def test_late_payment_after_expiry_is_refunded(ctx, seller, product):
    black = variant(product, "Black")
    order = ctx.checkout.create_checkout(product.id, black.id, 1, BUYER)
    ctx.checkout.expire_due(now() + timedelta(minutes=16))
    late = ctx.checkout.payment_succeeded(order.payment.reference)
    assert late.status == OrderStatus.EXPIRED and late.payment.status == "refunded"
    assert order.id in ctx.payment.refunds
    assert ctx.store.get_product(product.id).variant(black.id).stock.available == 5  # no phantom sale


def test_expiry_never_cancels_a_paid_order(ctx, seller, product):
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    assert ctx.checkout.expire_due(now() + timedelta(hours=2)) == []
    assert ctx.store.get_order(order.id).status == OrderStatus.PAID


def test_confirm_first_hold_follows_the_new_deadline(ctx, seller, product):
    navy = variant(product, "Navy")
    ctx.catalog.update_stock(seller.id, "Navy", 2)
    order = ctx.checkout.create_checkout(product.id, navy.id, 1, BUYER)
    order = ctx.checkout.seller_confirms_stock(seller.id, order.id, available=True)
    # the hold now ends with the QR code (15 min), not the 60-minute confirmation window
    assert ctx.checkout.expire_due(now() + timedelta(minutes=16)) == [order.id]


def test_failed_payment_creation_releases_stock(ctx, seller, product, monkeypatch):
    black = variant(product, "Black")

    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(ctx.payment, "create_payment", boom)
    with pytest.raises(CheckoutError, match="temporarily unavailable"):
        ctx.checkout.create_checkout(product.id, black.id, 2, BUYER)
    assert ctx.store.get_product(product.id).variant(black.id).stock.available == 5


def test_stale_product_copy_cannot_overwrite_stock(ctx, seller, product):
    black = variant(product, "Black")
    stale = ctx.store.get_product(product.id)
    ctx.checkout.create_checkout(product.id, black.id, 2, BUYER)
    stale.title = "Voal Hijab Premium"
    ctx.store.put_product(stale)  # e.g. a catalog edit that started before the checkout
    v = ctx.store.get_product(product.id).variant(black.id)
    assert ctx.store.get_product(product.id).title == "Voal Hijab Premium"
    assert (v.stock.on_hand, v.stock.reserved) == (5, 2)


# ---------------------------------------------------------------- shipping and payouts
def _shipped(ctx, seller, product):
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    return ctx.checkout.mark_shipped(seller.id, order.id, "JNE987654")


def test_unconfirmed_delivery_does_not_release_money(ctx, seller, product, monkeypatch):
    order = _shipped(ctx, seller, product)
    monkeypatch.setattr(ctx.shipping, "confirm_delivered", lambda o: False)
    result = ctx.checkout.shipment_update("JNE987654", "delivered")
    assert result.status == OrderStatus.SHIPPED and result.payout is None
    assert ctx.payment.releases == []


def test_manual_payout_when_provider_is_not_automated(ctx, seller, product, monkeypatch):
    _shipped(ctx, seller, product)

    def manual(*a, **k):
        raise ManualActionRequired("not automated")
    monkeypatch.setattr(ctx.payment, "release_to_seller", manual)
    order = ctx.checkout.shipment_update("JNE987654", "delivered")
    assert order.status == OrderStatus.DELIVERED and order.payout.status == "pending_manual"
    assert ctx.checkout.release_payout(order.id).payout.status == "pending_manual"  # no double release


def test_seller_waybill_must_look_like_one(ctx, seller, product):
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    with pytest.raises(CheckoutError):
        ctx.checkout.mark_shipped(seller.id, order.id, "<script>")


# ---------------------------------------------------------------- input limits
def test_checkout_input_limits(ctx, seller, product):
    black = variant(product, "Black")
    with pytest.raises(CheckoutError, match="at most"):
        ctx.checkout.create_checkout(product.id, black.id, 11, BUYER)
    with pytest.raises(CheckoutError, match="phone"):
        ctx.checkout.create_checkout(product.id, black.id, 1, BUYER.model_copy(update={"phone": "abc"}))
    with pytest.raises(CheckoutError, match="postal"):
        ctx.checkout.create_checkout(product.id, black.id, 1, BUYER.model_copy(update={"postal_code": "1"}))
    with pytest.raises(CheckoutError, match="longer"):
        ctx.checkout.create_checkout(product.id, black.id, 1, BUYER.model_copy(update={"name": "x" * 200}))


def test_checkouts_per_buyer_are_capped(ctx, seller, product):
    mocca = variant(product, "Mocca")
    for _ in range(ctx.settings.max_checkouts_per_buyer_per_hour):
        ctx.checkout.create_checkout(product.id, mocca.id, 1, buyer(1))
    with pytest.raises(CheckoutError, match="too many"):
        ctx.checkout.create_checkout(product.id, mocca.id, 1, buyer(1))
    ctx.checkout.create_checkout(product.id, mocca.id, 1, buyer(2))  # other buyers unaffected


def test_console_media_cannot_read_arbitrary_files(tmp_path):
    m = ConsoleMessenger(media_roots=[str(tmp_path)])
    with pytest.raises(PermissionError):
        m.download_media("/etc/passwd", str(tmp_path / "media"))
    ok = tmp_path / "photo.png"
    ok.write_bytes(b"x")
    assert m.download_media(str(ok), str(tmp_path / "media")) == str(ok.resolve())


def test_duplicate_whatsapp_delivery_is_ignored(ctx, seller):
    msg = Inbound(seller.whatsapp, "text", text="help", message_id="wamid.1")
    asyncio.run(ctx.inbox.handle(msg))
    asyncio.run(ctx.inbox.handle(msg))
    assert len(ctx.messenger.outbox) == 1


def test_connector_webhooks_only_to_public_https():
    for bad in ("http://example.com/hook", "https://127.0.0.1/hook", "https://169.254.169.254/latest",
                "https://localhost/hook"):
        with pytest.raises(ValueError):
            check_webhook_url(bad, allow_test=False)
    check_webhook_url("test://sink", allow_test=True)


def test_connector_resync_keeps_reservations(ctx, seller):
    conn, _ = ctx.connector.create_connection(seller.id, "connector_api")
    body = ProductIn(title="Arabica Coffee Beans 250 g", category="coffee",
                     variants=[VariantIn(external_id="v1", sku="ARA-250", price=90000, weight_gram=300, stock=10)])
    p = ctx.connector.upsert_product(conn, "p1", body)
    v = p.variants[0]
    ctx.store.reserve("o1", p.id, v.id, 3, now() + timedelta(minutes=15))
    body.variants[0].stock = 8
    p = ctx.connector.upsert_product(conn, "p1", body)
    assert (p.variants[0].stock.on_hand, p.variants[0].stock.reserved) == (8, 3)


def test_agents_get_urls_not_file_paths(ctx, seller, product):
    view = ctx.catalog.agent_view(product, seller)
    assert all(i.startswith("http") for i in view["images"]) and "/media/" in view["images"][0]
