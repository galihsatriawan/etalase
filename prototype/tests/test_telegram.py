"""Telegram as a second seller channel: the same orders, stock checks and replies, through a linked chat.
HTTP to Telegram is faked: these tests prove what we send, what we accept and who may link a chat, not that Telegram
accepts the bot."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from etalase import doctor
from etalase.adapters.messaging import ChannelRouter, ConsoleMessenger, Inbound
from etalase.adapters.telegram import SECRET_HEADER, TelegramError, TelegramMessenger, parse_telegram_update
from etalase.api.main import app
from etalase.api.main import ctx as app_ctx
from etalase.config import Settings
from etalase.models import OrderStatus
from etalase.security import InsecureConfig, validate_settings

from .conftest import BUYER, make_seller, variant
from .test_biteship import Resp
from .test_whatsapp import Recorder

OK = Resp(200, {"ok": True, "result": {"message_id": 1}})
SECRET = "s3cret-" + "x" * 20


def bot(**kw):
    return TelegramMessenger("123:TOKEN", **kw)


def tg_text(chat, text, update_id=1):
    return {"update_id": update_id, "message": {"message_id": 5, "chat": {"id": chat, "type": "private"}, "text": text}}


def link(ctx, seller, chat="555"):
    """Link `chat` the way a seller does: a code from the signed-in dashboard, then /start <code> from Telegram."""
    code = ctx.auth.link_code(seller.id)
    asyncio.run(ctx.inbox.handle(Inbound(f"tg:{chat}", "text", text=f"/start {code}", message_id=f"link{chat}{code[-6:]}")))
    return ctx.store.get_seller(seller.id)


def texts_to(ctx, address):
    return [m.text for m in ctx.messenger.outbox if m.to == address]


# ---------------------------------------------------------------- parsing
def test_text_and_button_updates_are_parsed():
    [m] = parse_telegram_update(tg_text(555, "stok navy 2"))
    assert (m.sender, m.kind, m.text, m.message_id) == ("tg:555", "text", "stok navy 2", "tg:1")
    [b] = parse_telegram_update({"update_id": 2, "callback_query": {
        "id": "cb9", "data": "confirm:ord_1", "message": {"chat": {"id": 555, "type": "private"}}}})
    assert (b.sender, b.kind, b.button_id, b.callback_id) == ("tg:555", "button", "confirm:ord_1", "cb9")


def test_photo_voice_and_files_are_parsed():
    chat = {"id": 555, "type": "private"}
    [img] = parse_telegram_update({"update_id": 3, "message": {"chat": chat, "caption": "Hijab voal, hitam 5", "photo": [
        {"file_id": "small", "width": 90}, {"file_id": "big", "width": 1280}]}})
    assert (img.kind, img.media_id, img.text) == ("image", "tg:big", "Hijab voal, hitam 5")   # the largest size wins
    [voice] = parse_telegram_update({"update_id": 4, "message": {"chat": chat, "voice": {"file_id": "v1", "mime_type": "audio/ogg"}}})
    assert (voice.kind, voice.media_id, voice.mime_type) == ("audio", "tg:v1", "audio/ogg")
    [csv] = parse_telegram_update({"update_id": 5, "message": {"chat": chat, "document": {"file_id": "d1", "mime_type": "text/csv"}}})
    assert csv.kind == "document"
    [pic] = parse_telegram_update({"update_id": 6, "message": {"chat": chat, "document": {"file_id": "d2", "mime_type": "image/png"}}})
    assert pic.kind == "image"                       # a picture sent "as a file" is still a product photo


def test_only_private_chats_count():
    group = {"update_id": 7, "message": {"chat": {"id": -100, "type": "supergroup"}, "text": "hi"}}
    assert parse_telegram_update(group) == []
    assert parse_telegram_update({"update_id": 8, "channel_post": {"chat": {"id": 1, "type": "channel"}, "text": "x"}}) == []
    assert parse_telegram_update({"update_id": 9, "edited_message": {"chat": {"id": 1, "type": "private"}, "text": "x"}}) == []
    assert parse_telegram_update({"update_id": 10, "message": {"chat": {"id": 1, "type": "private"}, "sticker": {}}}) == []
    assert parse_telegram_update({"update_id": 11, "callback_query": {"id": "c", "data": "x",
                                  "message": {"chat": {"id": -5, "type": "group"}}}}) == []


# ---------------------------------------------------------------- the Bot API calls
def test_send_text_matches_the_bot_api(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    bot().send_text("tg:555", "x" * 5000)
    url, kw = rec.calls[0]
    assert url == "https://api.telegram.org/bot123:TOKEN/sendMessage"
    assert kw["json"]["chat_id"] == "555" and len(kw["json"]["text"]) == 4096


def test_buttons_become_an_inline_keyboard_two_per_row(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    bot().send_buttons("tg:555", "Stock?", [("confirm:ord_1", "Available"), ("oos:ord_1", "Sold out"), ("x", "Third")])
    kb = rec.calls[0][1]["json"]["reply_markup"]["inline_keyboard"]
    assert kb == [[{"text": "Available", "callback_data": "confirm:ord_1"}, {"text": "Sold out", "callback_data": "oos:ord_1"}],
                  [{"text": "Third", "callback_data": "x"}]]
    with pytest.raises(ValueError):                  # Telegram allows 64 bytes; a longer id would silently break the button
        bot().send_buttons("tg:555", "x", [("a" * 65, "Too long")])


def test_notification_needs_no_template(monkeypatch):
    from etalase.adapters.messaging import Template
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    bot().send_notification("tg:555", "New order", [("accept:o", "Accept")], Template("new_paid_order", ["a"]))
    bot().send_notification("tg:555", "Payout")
    assert "reply_markup" in rec.calls[0][1]["json"] and "reply_markup" not in rec.calls[1][1]["json"]
    assert all("template" not in str(kw["json"]) for _, kw in rec.calls)


def test_errors_carry_telegrams_reason_and_never_the_token(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(Resp(403, {"ok": False, "error_code": 403,
                                                           "description": "Forbidden: bot was blocked by the user"})))
    with pytest.raises(TelegramError) as e:
        bot().send_text("tg:555", "hi")
    assert e.value.status == 403 and "blocked" in str(e.value) and "TOKEN" not in str(e.value)

    monkeypatch.setattr(httpx, "post", Recorder(Resp(429, {"ok": False, "description": "Too Many Requests",
                                                           "parameters": {"retry_after": 7}})))
    with pytest.raises(TelegramError) as e:
        bot().send_text("tg:555", "hi")
    assert e.value.retry_after == 7

    def boom(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url}")      # httpx puts the URL, and so the token, in the message
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(TelegramError) as e:
        bot().send_text("tg:555", "hi")
    assert "TOKEN" not in str(e.value) and "TOKEN" not in repr(e.value.__cause__)


def test_answer_callback_never_raises(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(Resp(400, {"ok": False, "description": "query is too old"})))
    bot().answer_callback("cb1")


def test_download_media_resolves_the_file_then_downloads_it(monkeypatch, tmp_path):
    rec = Recorder(Resp(200, {"ok": True, "result": {"file_id": "big", "file_size": 1000, "file_path": "photos/file_7.JPG"}}))
    monkeypatch.setattr(httpx, "post", rec)
    gets = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: gets.append(url) or type("R", (), {"status_code": 200, "content": b"IMG"})())
    path = bot().download_media("tg:big", str(tmp_path))
    assert rec.calls[0][1]["json"] == {"file_id": "big"}
    assert gets == ["https://api.telegram.org/file/bot123:TOKEN/photos/file_7.JPG"]
    assert path.endswith(".jpg") and open(path, "rb").read() == b"IMG"
    assert str(tmp_path) in path

    monkeypatch.setattr(httpx, "post", Recorder(Resp(200, {"ok": True, "result": {"file_path": "x/y.jpg", "file_size": 30 * 1024 * 1024}})))
    with pytest.raises(PermissionError, match="20 MB"):
        bot().download_media("tg:huge", str(tmp_path))


def test_bot_username_is_looked_up_once_and_the_webhook_is_set_with_the_secret(monkeypatch):
    rec = Recorder(Resp(200, {"ok": True, "result": {"username": "etalase_bot"}}))
    monkeypatch.setattr(httpx, "post", rec)
    b = bot()
    assert b.bot_username() == "etalase_bot" and b.bot_username() == "etalase_bot" and len(rec.calls) == 1
    assert bot(username="@given_bot").bot_username() == "given_bot"
    b.set_webhook("https://x.example/webhooks/telegram", SECRET)
    sent = rec.calls[-1][1]["json"]
    assert sent["secret_token"] == SECRET and sent["allowed_updates"] == ["message", "callback_query"]


# ---------------------------------------------------------------- one Messenger, two channels
def test_router_picks_the_channel_from_the_address(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    console = ConsoleMessenger()
    router = ChannelRouter(console, bot())
    router.send_text("6281234567890", "to whatsapp")
    router.send_buttons("tg:555", "to telegram", [("a", "A")])
    assert [m.to for m in console.outbox] == ["6281234567890"] and router.outbox is console.outbox
    assert len(rec.calls) == 1 and rec.calls[0][1]["json"]["chat_id"] == "555"


def test_the_seller_address_follows_the_chosen_channel(ctx, seller):
    assert seller.address == seller.whatsapp
    seller.telegram_chat_id = "555"
    assert seller.address == seller.whatsapp                       # linked but not chosen
    seller.channel = "telegram"
    assert seller.address == "tg:555"
    ctx.store.put_seller(seller)
    assert ctx.store.find_seller_by_address("tg:555").id == seller.id
    assert ctx.store.find_seller_by_address(seller.whatsapp).id == seller.id
    assert ctx.store.find_seller_by_address("tg:999") is None


# ---------------------------------------------------------------- linking a chat to a shop
def test_linking_with_a_dashboard_code(ctx, seller):
    linked = link(ctx, seller)
    assert linked.telegram_chat_id == "555" and linked.channel == "telegram" and linked.address == "tg:555"
    assert "Connected" in texts_to(ctx, "tg:555")[-1] and seller.name in texts_to(ctx, "tg:555")[-1]


def test_link_codes_fit_a_telegram_deep_link(ctx, seller):
    code = ctx.auth.link_code(seller.id)
    assert len(code) <= 64 and all(c.isalnum() or c in "_-" for c in code)
    assert ctx.auth.seller_from_link_code(code) == seller.id


def test_bad_expired_or_forged_codes_link_nothing(ctx, seller, monkeypatch):
    other = make_seller(ctx, "Toko Lain", "6281999000111")
    code = ctx.auth.link_code(seller.id)
    forged = code.replace(seller.id, other.id, 1)                  # another shop's id under this signature
    for bad in (forged, "garbage", code[:-3] + "xyz", ""):
        assert ctx.auth.seller_from_link_code(bad) is None
        asyncio.run(ctx.inbox.handle(Inbound("tg:666", "text", text=f"/start {bad}", message_id=f"m{len(bad)}{bad[-3:]}")))
        assert "expired or is not valid" in texts_to(ctx, "tg:666")[-1] or "not linked" in texts_to(ctx, "tg:666")[-1]
    assert ctx.store.get_seller(seller.id).telegram_chat_id is None and ctx.store.get_seller(other.id).telegram_chat_id is None
    import etalase.auth as auth_module
    real = auth_module.time.time
    monkeypatch.setattr(auth_module.time, "time", lambda: real() + 11 * 60)
    assert ctx.auth.seller_from_link_code(code) is None            # 10 minutes


def test_a_linked_chat_cannot_be_moved_to_another_shop_by_a_code(ctx, seller):
    other = make_seller(ctx, "Toko Lain", "6281999000111")
    link(ctx, seller, "555")
    link(ctx, other, "555")                                        # chat 555 already belongs to the first shop
    assert ctx.store.get_seller(other.id).telegram_chat_id is None
    assert ctx.store.find_seller_by_address("tg:555").id == seller.id


def test_a_stranger_gets_one_short_pointer_not_a_conversation(ctx, seller):
    for i in range(6):
        asyncio.run(ctx.inbox.handle(Inbound("tg:777", "text", text="hello", message_id=f"s{i}")))
    replies = texts_to(ctx, "tg:777")
    assert len(replies) == 3 and "not linked" in replies[0] and "Connect Telegram" in replies[0]   # capped per hour
    assert ctx.store.list_products() == []


# ---------------------------------------------------------------- the seller's day, on Telegram
def test_an_order_reaches_the_seller_on_telegram_and_a_button_press_answers_it(ctx, seller, product):
    link(ctx, seller)
    ctx.catalog.update_stock(seller.id, "Navy", 2)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Navy").id, 1, BUYER)
    assert order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION
    ask = [m for m in ctx.messenger.outbox if m.to == "tg:555" and m.buttons][-1]
    assert [b[0] for b in ask.buttons] == [f"confirm:{order.id}", f"oos:{order.id}"] and "Navy" in ask.text
    assert not [m for m in ctx.messenger.outbox if m.to == seller.whatsapp and m.buttons]          # not sent twice
    assert all(len(b[0].encode()) <= 64 for b in ask.buttons)

    [press] = parse_telegram_update({"update_id": 20, "callback_query": {
        "id": "cb1", "data": f"confirm:{order.id}", "message": {"chat": {"id": 555, "type": "private"}}}})
    asyncio.run(ctx.inbox.handle(press))
    assert ctx.store.get_order(order.id).status == OrderStatus.AWAITING_PAYMENT
    assert "QRIS" in texts_to(ctx, "tg:555")[-1]


def test_a_paid_order_and_the_payout_arrive_on_telegram_too(ctx, seller, product):
    from .conftest import after_window
    link(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, variant(product, "Black").id, 1, BUYER)
    ctx.checkout.payment_succeeded(order.payment.reference)
    assert "New paid order" in texts_to(ctx, "tg:555")[-1]
    ctx.checkout.seller_accepts(seller.id, order.id)
    ctx.checkout.mark_shipped(seller.id, None, "JNE123456")
    ctx.checkout.shipment_update("JNE123456", "delivered")
    after_window(ctx)
    assert any("paid out" in t.lower() or "payout" in t.lower() for t in texts_to(ctx, "tg:555"))
    assert not texts_to(ctx, seller.whatsapp)       # nothing went to WhatsApp once Telegram was chosen


def test_replies_go_back_through_the_channel_the_seller_wrote_on(ctx, seller):
    link(ctx, seller)                                              # channel is now telegram
    asyncio.run(ctx.inbox.handle(Inbound(seller.whatsapp, "text", text="link toko", message_id="w1")))
    assert any("shop link" in t.lower() for t in texts_to(ctx, seller.whatsapp))     # wrote on WhatsApp: answered there
    asyncio.run(ctx.inbox.handle(Inbound("tg:555", "text", text="link toko", message_id="t1")))
    assert any("shop link" in t.lower() for t in texts_to(ctx, "tg:555"))

    seller = ctx.store.get_seller(seller.id)
    seller.channel = "whatsapp"
    ctx.store.put_seller(seller)
    before = len(texts_to(ctx, seller.whatsapp))
    asyncio.run(ctx.inbox.handle(Inbound("tg:555", "text", text="link toko", message_id="t2")))
    assert len(texts_to(ctx, seller.whatsapp)) == before            # still answered on Telegram
    assert len([t for t in texts_to(ctx, "tg:555") if "shop link" in t.lower()]) == 2


def test_a_photo_from_telegram_builds_a_draft(ctx, seller, monkeypatch):
    link(ctx, seller)
    seen = []
    monkeypatch.setattr(ctx.messenger, "download_media", lambda media_id, dest: seen.append(media_id) or "samples/voal-hijab.png")
    asyncio.run(ctx.inbox.handle(Inbound("tg:555", "image", media_id="tg:big", text="Voal hijab, black 5, price 85k", message_id="p1")))
    assert seen == ["tg:big"]
    assert texts_to(ctx, "tg:555")[-1].startswith("I found 1 product") and ctx.messenger.outbox[-1].buttons[0][0] == "publish"


def test_the_sign_in_code_goes_to_the_chosen_channel(ctx, seller):
    ctx.auth.request(seller.whatsapp)
    assert "sign-in code" in texts_to(ctx, seller.whatsapp)[-1]
    link(ctx, seller)
    ctx.auth._limits = type(ctx.auth._limits)()                    # skip the resend cooldown
    ctx.auth.request(seller.whatsapp)
    code = texts_to(ctx, "tg:555")[-1].split(": ")[1][:6]
    assert ctx.auth.verify(seller.whatsapp, code)


# ---------------------------------------------------------------- over HTTP
@pytest.fixture(scope="module")
def c():
    return TestClient(app)    # no lifespan: it would start the MCP session manager a second time in this process


def register(c, wa):
    c.post("/dev/sellers", json={"name": "Telegram Shop", "whatsapp": wa})
    c.post("/auth/otp", json={"whatsapp": wa})
    code = app_ctx.messenger.outbox[-1].text.split(": ")[1][:6]
    return {"Authorization": "Bearer " + c.post("/auth/verify", json={"whatsapp": wa, "code": code}).json()["token"]}


def test_the_dashboard_links_telegram_and_switches_channel(c):
    h = register(c, "6287700000031")
    me = c.get("/api/me", headers=h).json()
    assert me["channel"] == "whatsapp" and me["telegram"] == {"available": True, "linked": False}
    assert c.put("/api/me/channel", headers=h, json={"channel": "telegram"}).status_code == 400    # nothing linked yet
    assert c.put("/api/me/channel", headers=h, json={"channel": "fax"}).status_code == 400
    assert c.post("/api/me/telegram/link").status_code == 401                                      # needs a signed-in seller

    link_info = c.post("/api/me/telegram/link", headers=h).json()
    assert link_info["command"] == f"/start {link_info['code']}" and link_info["expires_in"] == 600
    r = c.post("/webhooks/telegram", json=tg_text(4242, link_info["command"], update_id=900))
    assert r.status_code == 200
    me = c.get("/api/me", headers=h).json()
    assert me["channel"] == "telegram" and me["telegram"]["linked"] is True
    assert "Connected" in [m.text for m in app_ctx.messenger.outbox if m.to == "tg:4242"][-1]

    back = c.put("/api/me/channel", headers=h, json={"channel": "whatsapp"}).json()
    assert back["channel"] == "whatsapp" and back["telegram"]["linked"] is True
    assert "from now on" in app_ctx.messenger.outbox[-1].text and app_ctx.messenger.outbox[-1].to == "6287700000031"


def test_the_webhook_needs_the_secret_when_a_real_bot_is_configured(c, monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr(app_ctx, "telegram", bot())
    monkeypatch.setattr(app_ctx.settings, "telegram_webhook_secret", None)
    assert c.post("/webhooks/telegram", json=tg_text(1, "hi")).status_code == 503
    monkeypatch.setattr(app_ctx.settings, "telegram_webhook_secret", SECRET)
    assert c.post("/webhooks/telegram", json=tg_text(1, "hi")).status_code == 401
    assert c.post("/webhooks/telegram", headers={SECRET_HEADER: "wrong"}, json=tg_text(1, "hi")).status_code == 401
    assert c.post("/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=tg_text(1, "hi")).status_code == 200


def test_a_button_press_is_acknowledged_with_telegram(c, monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    monkeypatch.setattr(app_ctx, "telegram", bot())
    monkeypatch.setattr(app_ctx.settings, "telegram_webhook_secret", SECRET)
    body = {"update_id": 950, "callback_query": {"id": "cbX", "data": "edit",
                                                "message": {"chat": {"id": 31337, "type": "private"}}}}
    assert c.post("/webhooks/telegram", headers={SECRET_HEADER: SECRET}, json=body).status_code == 200
    assert rec.calls[0][0].endswith("/answerCallbackQuery") and rec.calls[0][1]["json"] == {"callback_query_id": "cbX"}


def test_a_failing_update_is_still_acknowledged(c, monkeypatch):
    async def boom(msg):
        raise RuntimeError("handler bug")
    monkeypatch.setattr(app_ctx.inbox, "handle", boom)
    assert c.post("/webhooks/telegram", json=tg_text(2, "hi", update_id=960)).status_code == 200   # or Telegram redelivers forever


# ---------------------------------------------------------------- configuration and doctor
def test_a_bot_token_needs_a_valid_webhook_secret():
    modes = {"messaging": "console", "payment": "mock", "shipping": "mock", "store": "memory", "ai": "offline rules"}
    for secret in (None, "short", "has spaces and is long enough!!"):
        s = Settings(etalase_env="dev", telegram_bot_token="123:abc", telegram_webhook_secret=secret, _env_file=None)
        with pytest.raises(InsecureConfig, match="TELEGRAM_WEBHOOK_SECRET"):
            validate_settings(s, modes)
    validate_settings(Settings(etalase_env="dev", telegram_bot_token="123:abc", telegram_webhook_secret=SECRET, _env_file=None), modes)


def test_doctor_reports_the_telegram_bot(monkeypatch):
    def checks(**kw):
        return {c.name: c for c in doctor.run_checks(Settings(etalase_env="dev", _env_file=None, **kw), live=True)}
    assert checks()["telegram"].status == doctor.SKIP
    assert checks(telegram_bot_token="1:a")["telegram"].status == doctor.FAIL                    # no secret
    assert checks(telegram_bot_token="1:a", telegram_webhook_secret=SECRET)["urls"].detail.count("/webhooks/telegram") == 1

    def fake(url, **kw):
        if url.endswith("/getMe"):
            return Resp(200, {"ok": True, "result": {"username": "etalase_bot"}})
        return Resp(200, {"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(httpx, "post", fake)
    unset = checks(telegram_bot_token="1:a", telegram_webhook_secret=SECRET)["telegram"]
    assert unset.status == doctor.WARN and "set-webhook" in unset.hint

    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp(200, {"ok": True, "result": {"username": "etalase_bot", "url": "https://x/webhooks/telegram"}}))
    assert checks(telegram_bot_token="1:a", telegram_webhook_secret=SECRET)["telegram"].status == doctor.OK

    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp(401, {"ok": False, "description": "Unauthorized"}))
    bad = checks(telegram_bot_token="1:secrettoken", telegram_webhook_secret=SECRET)["telegram"]
    assert bad.status == doctor.FAIL and "secrettoken" not in bad.detail
