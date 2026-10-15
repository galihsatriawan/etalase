"""Regression tests for the findings of the independent security and edge-case review of the Telegram, shop-address and
returns work. Each test reproduces the failure first reported (a leaked token, a replayed link code, two shops on one address,
a stolen waybill, a lost refund, a locked-out seller, a Host bypass, an unbounded upload) and pins the fix."""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from etalase import security
from etalase.adapters.messaging import ChannelRouter, ConsoleMessenger, Inbound
from etalase.adapters.telegram import TelegramError, TelegramMessenger
from etalase.api.main import MAX_BODY_BYTES, app
from etalase.api.main import ctx as app_ctx
from etalase.config import Settings
from etalase.container import build_context
from etalase.models import CheckoutError, OrderStatus
from etalase.seller import slugs

from .conftest import BUYER, make_product, make_seller, variant
from .test_biteship import Resp
from .test_telegram import link, texts_to, tg_text
from .test_whatsapp import Recorder


# ---------------------------------------------------------------- 1. the bot token must not reach the logs
def test_the_bot_token_is_not_written_to_the_log(caplog):
    """httpx logs every request URL at INFO, the Bot API puts the token in the URL, and FastMCP sets the root logger to INFO."""
    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            body = b'{"ok": true, "result": {"message_id": 1}}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        caplog.set_level(logging.INFO)
        TelegramMessenger("123456:SECRET-BOT-TOKEN", api=f"http://127.0.0.1:{server.server_port}").send_text("tg:1", "hi")
    finally:
        server.shutdown()
    assert "SECRET-BOT-TOKEN" not in caplog.text and "/bot" not in caplog.text


# ---------------------------------------------------------------- 2. link codes work once, and a relink is announced
def test_a_link_code_works_once(ctx, seller):
    code = ctx.auth.link_code(seller.id)
    asyncio.run(ctx.inbox.handle(Inbound("tg:111", "text", text=f"/start {code}", message_id="a1")))
    asyncio.run(ctx.inbox.handle(Inbound("tg:999", "text", text=f"/start {code}", message_id="a2")))   # a copy of the same code
    assert ctx.store.get_seller(seller.id).telegram_chat_id == "111"
    assert "expired or is not valid" in texts_to(ctx, "tg:999")[-1]
    assert ctx.auth.seller_from_link_code(code) is None and ctx.auth.use_link_code(code) is None


def test_a_code_cannot_be_used_twice_at_the_same_moment(ctx, seller):
    code = ctx.auth.link_code(seller.id)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: ctx.auth.use_link_code(code), range(8)))
    assert results.count(seller.id) == 1 and results.count(None) == 7


def test_replacing_the_linked_chat_tells_the_old_chat_and_the_whatsapp_number(ctx, seller):
    link(ctx, seller, "111")
    link(ctx, seller, "222")           # a second, fresh code from the signed-in dashboard
    assert ctx.store.get_seller(seller.id).telegram_chat_id == "222"
    assert "no longer gets orders" in texts_to(ctx, "tg:111")[-1]
    assert "no longer gets orders" in texts_to(ctx, seller.whatsapp)[-1]


def test_a_stranger_cannot_make_the_bot_talk_endlessly(ctx, seller):
    for i in range(30):
        asyncio.run(ctx.inbox.handle(Inbound("tg:777", "text", text="/start garbage", message_id=f"g{i}")))
        asyncio.run(ctx.inbox.handle(Inbound("6289900000001", "text", text="hello", message_id=f"w{i}")))
    assert len(texts_to(ctx, "tg:777")) <= 5
    assert len(texts_to(ctx, "6289900000001")) <= 3


# ---------------------------------------------------------------- 3. one shop address, one shop
def test_two_shops_racing_for_one_address_cannot_both_win(ctx, monkeypatch):
    real = ctx.store.find_seller_by_slug

    def slow_lookup(slug):          # the gap between "is it free?" and "save it", as a networked database has
        found = real(slug)
        threading.Event().wait(0.02)
        return found
    monkeypatch.setattr(ctx.store, "find_seller_by_slug", slow_lookup)
    for round_ in range(5):
        a = make_seller(ctx, f"Race A{round_}", f"62812000000{round_}1")
        b = make_seller(ctx, f"Race B{round_}", f"62812000000{round_}2")
        outcome = []

        def go(seller_id):
            try:
                slugs.claim(ctx, seller_id, f"contested-{round_}")
                outcome.append(seller_id)
            except slugs.SlugError:
                pass
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(go, [a.id, b.id]))
        assert len(outcome) == 1
        owners = [s for s in (ctx.store.get_seller(a.id), ctx.store.get_seller(b.id)) if s.slug == f"contested-{round_}"]
        assert [s.id for s in owners] == outcome


def test_a_released_address_is_not_handed_to_a_stranger_at_once(ctx):
    a, b = make_seller(ctx, "Old Owner", "6281200000011"), make_seller(ctx, "New Owner", "6281200000012")
    slugs.claim(ctx, a.id, "rina-hijab")
    slugs.claim(ctx, a.id, "rina-store")
    with pytest.raises(slugs.SlugError, match="taken"):
        slugs.claim(ctx, b.id, "rina-hijab")
    assert slugs.claim(ctx, a.id, "rina-hijab").slug == "rina-hijab"


def test_the_platform_host_label_cannot_be_claimed_as_a_shop(ctx, monkeypatch):
    monkeypatch.setattr(ctx.settings, "store_base_domain", "etalase.test")
    monkeypatch.setattr(ctx.settings, "public_base_url", "https://console.etalase.test")
    seller = make_seller(ctx, "Console Shop", "6281200000013")
    with pytest.raises(slugs.SlugError, match="reserved"):
        slugs.claim(ctx, seller.id, "console")


# ---------------------------------------------------------------- 4. a tracking number belongs to one order
def paid_accepted(ctx, seller, product):
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    return order


def test_another_shops_waybill_cannot_be_reused(ctx):
    honest, thief = make_seller(ctx, "Honest", "6281300000001"), make_seller(ctx, "Thief", "6281300000002")
    p1, p2 = make_product(ctx, honest), make_product(ctx, thief)
    o1, o2 = paid_accepted(ctx, honest, p1), paid_accepted(ctx, thief, p2)
    ctx.checkout.mark_shipped(honest.id, o1.id, "JNE0012345678")
    with pytest.raises(CheckoutError, match="already used"):
        ctx.checkout.mark_shipped(thief.id, o2.id, "jne0012345678")
    assert ctx.store.get_order(o2.id).status == OrderStatus.ACCEPTED and ctx.store.get_order(o2.id).shipment is None
    ctx.checkout.shipment_update("JNE0012345678", "delivered")      # the honest parcel's delivery starts the honest payout
    assert ctx.store.get_order(o1.id).status == OrderStatus.DELIVERED
    assert ctx.store.get_order(o2.id).status == OrderStatus.ACCEPTED


def test_an_order_can_be_given_the_same_waybill_again(ctx, seller, product):
    order = paid_accepted(ctx, seller, product)
    assert ctx.checkout.mark_shipped(seller.id, order.id, "JNE0099999999").shipment.waybill == "JNE0099999999"


# ---------------------------------------------------------------- 5. a stock error must not lose the alert or the refund
def test_a_stock_error_after_payment_still_tells_the_seller(ctx, seller, product, monkeypatch):
    events = []
    ctx.events.subscribe("ops.manual_action", events.append)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    monkeypatch.setattr(ctx.store, "commit", lambda order_id: (_ for _ in ()).throw(KeyError("variant dropped by a resync")))
    paid = ctx.checkout.payment_succeeded(order.payment.reference)
    assert paid.status == OrderStatus.PAID
    assert any("New paid order" in t for t in texts_to(ctx, seller.whatsapp))
    assert {"order_id": order.id, "action": "commit_stock"} in events


def test_a_stock_error_during_a_rejection_does_not_skip_the_refund(ctx, seller, product, monkeypatch):
    events = []
    ctx.events.subscribe("ops.manual_action", events.append)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    monkeypatch.setattr(ctx.store, "restock", lambda order_id: (_ for _ in ()).throw(KeyError("gone")))
    monkeypatch.setattr(ctx.store, "set_stock", lambda *a, **k: (_ for _ in ()).throw(KeyError("gone")))
    done = ctx.checkout.seller_rejects_paid(seller.id, order.id)
    assert done.payment.status == "refunded" and done.payment.refunded_amount == done.total
    assert {"order_id": order.id, "action": "restock"} in events and {"order_id": order.id, "action": "set_stock"} in events


# ---------------------------------------------------------------- 6. a dead Telegram chat must not lock the seller out
@pytest.fixture
def bot_ctx():
    return build_context(Settings(etalase_env="dev", telegram_bot_token="123:TOKEN", telegram_webhook_secret="w" * 20,
                                  _env_file=None))


def linked_seller(ctx, chat="555"):
    seller = make_seller(ctx, "Telegram Shop", "6281400000001")
    seller.telegram_chat_id, seller.channel = chat, "telegram"
    ctx.store.put_seller(seller)
    return seller


def test_the_sign_in_code_falls_back_to_whatsapp_when_the_chat_is_blocked(bot_ctx, monkeypatch):
    seller = linked_seller(bot_ctx)
    monkeypatch.setattr(httpx, "post", Recorder(Resp(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})))
    bot_ctx.auth.request(seller.whatsapp)
    assert any("sign-in code" in m.text for m in bot_ctx.messenger.outbox if m.to == seller.whatsapp)


def test_order_alerts_fall_back_to_whatsapp_too(bot_ctx, monkeypatch):
    seller = linked_seller(bot_ctx)
    product = make_product(bot_ctx, seller)
    monkeypatch.setattr(httpx, "post", Recorder(Resp(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})))
    order = bot_ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    bot_ctx.checkout.payment_succeeded(order.payment.reference)
    assert any("New paid order" in m.text for m in bot_ctx.messenger.outbox if m.to == seller.whatsapp)


def test_temporary_telegram_trouble_is_not_a_reason_to_switch_channel(bot_ctx, monkeypatch):
    seller = linked_seller(bot_ctx)
    monkeypatch.setattr(httpx, "post", Recorder(Resp(429, {"ok": False, "description": "Too Many Requests"})))
    with pytest.raises(TelegramError):
        bot_ctx.messenger.send_text(seller.address, "hi")
    assert not [m for m in bot_ctx.messenger.outbox if m.to == seller.whatsapp]


def test_the_router_without_a_fallback_behaves_as_before(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(Resp(403, {"ok": False, "description": "blocked"})))
    router = ChannelRouter(ConsoleMessenger(), TelegramMessenger("1:T"))
    with pytest.raises(TelegramError):
        router.send_text("tg:5", "hi")


# ---------------------------------------------------------------- 7. the Host header cannot bypass the shop mapping
DOMAIN = "etalase.test"


@pytest.fixture
def shops(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", DOMAIN)
    monkeypatch.setattr(app_ctx.settings, "public_base_url", f"http://app.{DOMAIN}")
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/dev/sellers", json={"name": "Host Probe Shop", "whatsapp": "62877100000" + "51"}).json()
    return client, created["slug"]


def kind(response):
    if 'id="login"' in response.text:
        return "dashboard"
    return "store" if "Host Probe Shop" in response.text else f"{response.status_code}"


@pytest.mark.parametrize("host", ["nobody.etalase.test", "nobody.etalase.test.", "nobody.etalase.test:443", "NOBODY.Etalase.Test",
                                  "nobody.etalase.test:8443", "a.b.etalase.test", "x.nobody.etalase.test."])
def test_a_name_nobody_claimed_is_never_the_dashboard(shops, host):
    client, _ = shops
    r = client.get("/", headers={"host": host})
    assert r.status_code == 404 and kind(r) == "404"
    assert "'unsafe-inline'" not in r.headers.get("content-security-policy", "")


@pytest.mark.parametrize("suffix", ["", ".", ":443", ":80", ":8443"])
def test_a_claimed_shop_answers_on_every_spelling_of_its_host(shops, suffix):
    client, slug = shops
    r = client.get("/", headers={"host": f"{slug}.{DOMAIN}{suffix}"})
    assert kind(r) == "store"


@pytest.mark.parametrize("host", [f"app.{DOMAIN}", f"app.{DOMAIN}.", DOMAIN, "testserver", "evil.example"])
def test_the_platform_host_and_other_domains_keep_their_behaviour(shops, host):
    client, _ = shops
    assert kind(client.get("/", headers={"host": host})) == "dashboard"


# ---------------------------------------------------------------- 8. money endpoints use their own header-only secret
def request_with(headers=None, query=b"", client=("203.0.113.9", 1234)):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": query, "client": client,
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def test_operator_endpoints_need_the_operator_token_in_a_header():
    s = Settings(etalase_env="dev", public_base_url="https://shop.example.com", operator_token="o" * 24, webhook_token="w" * 24,
                 _env_file=None)
    security.require_operator_token(request_with({"X-Etalase-Operator-Token": "o" * 24}), s)
    for bad in (request_with(), request_with(query=b"token=" + b"o" * 24), request_with({"X-Etalase-Webhook-Token": "w" * 24}),
                request_with({"X-Etalase-Operator-Token": "x" * 24})):
        with pytest.raises(Exception) as e:
            security.require_operator_token(bad, s)
        assert e.value.status_code == 401


def test_operator_endpoints_are_closed_without_a_configured_token_on_a_public_deployment():
    public = Settings(etalase_env="dev", public_base_url="https://shop.example.com", _env_file=None)
    with pytest.raises(Exception) as e:
        security.require_operator_token(request_with(), public)
    assert e.value.status_code == 503
    security.require_operator_token(request_with(), Settings(etalase_env="dev", _env_file=None))      # local dev run


# ---------------------------------------------------------------- 9. the client address behind a proxy
def test_the_client_address_skips_the_trusted_proxy_entries(monkeypatch):
    header = {"X-Forwarded-For": "6.6.6.6, 198.51.100.7, 35.191.0.1"}
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", 0)
    assert security.client_ip(request_with(header)) == "35.191.0.1"          # Cloud Run alone: the last entry is the client
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", 1)
    assert security.client_ip(request_with(header)) == "198.51.100.7"        # behind a load balancer: skip its address
    assert security.client_ip(request_with({"X-Forwarded-For": "9.9.9.9"})) == "9.9.9.9"      # fewer entries than hops
    assert security.client_ip(request_with()) == "203.0.113.9"                # no header: the socket peer


# ---------------------------------------------------------------- 10. an upload without Content-Length is capped too
def chunks(total, size=64 * 1024):
    sent = 0
    while sent < total:
        piece = min(size, total - sent)
        sent += piece
        yield b"x" * piece


def test_a_chunked_upload_over_the_limit_is_refused():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/webhooks/telegram", content=chunks(MAX_BODY_BYTES + 100_000), headers={"content-type": "application/json"})
    assert r.status_code == 413
    r = client.post("/webhooks/telegram", content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413                                                  # by Content-Length
    small = client.post("/webhooks/telegram", content=chunks(2000), headers={"content-type": "application/json"})
    assert small.status_code == 200                                              # a small chunked body is fine


# ---------------------------------------------------------------- 11. a malformed webhook body is acknowledged, not a 500
@pytest.mark.parametrize("body", [b"[]", b"[1,2]", b"not json", b'"text"', b"null",
                                  b'{"update_id":1,"message":{"chat":{"id":1,"type":"private"},"photo":[{"width":9}]}}',
                                  b'{"update_id":1,"message":{"chat":"private","text":"x"}}',
                                  b'{"update_id":1,"callback_query":{"id":"c","data":"x","message":{"chat":{"type":"private"}}}}'])
def test_malformed_telegram_updates_are_acknowledged(body):
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/webhooks/telegram", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True}


@pytest.mark.parametrize("body", [b"[]", b"not json", b'{"entry": "x"}', b'{"entry":[{"changes":[{"value":{"messages":[{"from":"1"}]}}]}]}'])
def test_malformed_whatsapp_bodies_are_acknowledged(body):
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/webhooks/whatsapp", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True}


# ---------------------------------------------------------------- 12. a buyer cannot put a clickable link in the seller's chat
def test_links_in_buyer_text_are_not_pushed_into_the_sellers_chat(ctx, seller, product):
    buyer = BUYER.model_copy(update={"line1": "Jl. Dago 10 https://evil.example/pay", "city": "Bandung www.evil.example"})
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, buyer)
    ctx.checkout.payment_succeeded(order.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order.id)
    ctx.checkout.mark_shipped(seller.id, None, "JNE0077777777")
    ctx.checkout.shipment_update("JNE0077777777", "delivered")
    ctx.returns.request(order.id, buyer.phone, "damaged", "see https://evil.example/photos and HTTP://x.example/a", None)
    sent = " ".join(m.text for m in ctx.messenger.outbox if m.to == seller.whatsapp)
    assert "evil.example" not in sent and "x.example" not in sent and "[link removed]" in sent
