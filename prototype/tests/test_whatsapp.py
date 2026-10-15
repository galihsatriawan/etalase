"""WhatsApp Cloud API contract, pinned from Meta's documentation (checked 2026-10-15), and the 24-hour-window fallback.
HTTP is faked: these tests prove what we send and how we react to Meta's errors, not that Meta accepts the account."""

import re

import httpx
import pytest

from etalase.adapters.messaging import (ConsoleMessenger, Template, WhatsAppCloudMessenger, WhatsAppError,
                                         whatsapp_failed_statuses)
from etalase.adapters.whatsapp_templates import LANGUAGES, TEMPLATES, clean_param, template_name

from .conftest import BUYER, make_product, make_seller, variant
from .test_biteship import Resp


def meta_error(code, message="x"):
    return Resp(400, {"error": {"message": message, "type": "OAuthException", "code": code, "error_data": {"details": message}}})


class Recorder:
    """Fake httpx.post: records each call and answers from a list of responses (the last one repeats)."""

    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


OK = Resp(200, {"messaging_product": "whatsapp", "contacts": [{"input": "x", "wa_id": "x"}], "messages": [{"id": "wamid.1"}]})


def messenger(**kw):
    return WhatsAppCloudMessenger("tok", "PHONE_ID", **kw)


def test_default_api_version_is_the_current_one():
    assert WhatsAppCloudMessenger("t", "p").base.endswith("/v26.0")


def test_text_message_matches_the_documented_request(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    messenger().send_text("6281234567890", "x" * 5000)
    url, kw = rec.calls[0]
    assert url == "https://graph.facebook.com/v26.0/PHONE_ID/messages"
    assert kw["headers"]["Authorization"] == "Bearer tok"
    body = kw["json"]
    assert body["messaging_product"] == "whatsapp" and body["recipient_type"] == "individual" and body["type"] == "text"
    assert body["to"] == "+6281234567890"                  # Meta recommends the plus sign and country code
    assert len(body["text"]["body"]) == 4096               # documented maximum


def test_reply_buttons_respect_the_documented_limits(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    messenger().send_buttons("+6281234567890", "b" * 2000, [("a:1", "A" * 40), ("b:2", "B"), ("c:3", "C"), ("d:4", "D")])
    body = rec.calls[0][1]["json"]
    assert body["to"] == "+6281234567890"                  # no double plus
    assert len(body["interactive"]["body"]["text"]) == 1024
    buttons = body["interactive"]["action"]["buttons"]
    assert len(buttons) == 3 and len(buttons[0]["reply"]["title"]) == 20      # at most 3 buttons, titles up to 20
    assert buttons[0] == {"type": "reply", "reply": {"id": "a:1", "title": "A" * 20}}


def test_inside_the_window_a_notification_is_sent_free_form_and_no_template_is_used(monkeypatch):
    rec = Recorder(OK)
    monkeypatch.setattr(httpx, "post", rec)
    messenger().send_notification("6281234567890", "hello", [("confirm:1", "Ada")], Template("stock_check", ["a", "b"], ["confirm:1", "oos:1"]))
    assert len(rec.calls) == 1 and rec.calls[0][1]["json"]["type"] == "interactive"


def test_outside_the_window_the_notification_falls_back_to_the_template_with_quick_reply_payloads(monkeypatch):
    rec = Recorder(meta_error(131047, "Re-engagement message"), OK)
    monkeypatch.setattr(httpx, "post", rec)
    tpl = Template("stock_check", ["Voal Hijab\n(Black)   x 2", "Bandung"], ["confirm:ord_1", "oos:ord_1"])
    messenger(template_language="id").send_notification("6281234567890", "Cek stok", [("confirm:ord_1", "Ada")], tpl)
    assert len(rec.calls) == 2
    sent = rec.calls[1][1]["json"]
    assert sent["type"] == "template" and sent["to"] == "+6281234567890"
    assert sent["template"] == {"name": "etalase_stock_check", "language": {"code": "id"}, "components": [
        {"type": "body", "parameters": [{"type": "text", "text": "Voal Hijab (Black) x 2"}, {"type": "text", "text": "Bandung"}]},
        {"type": "button", "sub_type": "quick_reply", "index": "0", "parameters": [{"type": "payload", "payload": "confirm:ord_1"}]},
        {"type": "button", "sub_type": "quick_reply", "index": "1", "parameters": [{"type": "payload", "payload": "oos:ord_1"}]}]}


def test_other_errors_are_not_retried_as_templates(monkeypatch):
    rec = Recorder(meta_error(190, "Invalid OAuth access token"))
    monkeypatch.setattr(httpx, "post", rec)
    with pytest.raises(WhatsAppError) as e:
        messenger().send_notification("6281234567890", "x", template=Template("payout", ["a", "b"]))
    assert e.value.code == 190 and len(rec.calls) == 1


def test_without_a_template_a_closed_window_is_an_error_the_caller_can_see(monkeypatch):
    monkeypatch.setattr(httpx, "post", Recorder(meta_error(131047)))
    with pytest.raises(WhatsAppError) as e:
        messenger().send_notification("6281234567890", "x")
    assert e.value.code == 131047


def test_template_variables_are_flattened():
    assert clean_param("a\nb\t c      d") == "a b c d"


def test_failed_delivery_statuses_are_surfaced():
    body = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.9", "status": "failed", "recipient_id": "6281", "errors": [{"code": 131047, "title": "Re-engagement message"}]},
        {"id": "wamid.8", "status": "delivered", "recipient_id": "6281"}]}}]}]}
    assert whatsapp_failed_statuses(body) == [{"to": "6281", "message_id": "wamid.9", "code": 131047, "title": "Re-engagement message"}]


# ---------------------------------------------------------------- the notifications use the template definitions correctly
def seller_flow(ctx):
    seller = make_seller(ctx)
    product = make_product(ctx, seller)
    return seller, product, variant(product, "Black")


def collect_templates(ctx):
    return [m.template for m in ctx.messenger.outbox if m.template]


def test_every_server_started_notification_carries_a_template_that_matches_its_definition(ctx):
    seller, product, v = seller_flow(ctx)
    ctx.store.set_stock(seller.id, product.id, v.id, None, "chat")                     # confirm-first: stock check goes out
    order = ctx.checkout.create_checkout(product.id, v.id, 1, BUYER)
    ctx.checkout.seller_confirms_stock(seller.id, order.id, available=True)
    ctx.checkout.payment_succeeded(order.payment.reference if order.payment else ctx.store.get_order(order.id).payment.reference)
    ctx.store.set_stock(seller.id, product.id, v.id, 5, "chat")
    order2 = ctx.checkout.create_checkout(product.id, v.id, 1, BUYER)                  # instant: paid order with buttons
    ctx.checkout.payment_succeeded(order2.payment.reference)
    ctx.checkout.seller_accepts(seller.id, order2.id)
    ctx.checkout.mark_shipped(seller.id, order2.id, "JNE123456")
    ctx.checkout.shipment_update("JNE123456", "delivered")
    seen = {t.key: t for t in collect_templates(ctx)}
    assert {"stock_check", "new_paid_order", "paid_ship_now", "payout"} <= set(seen)
    for key, tpl in seen.items():
        spec = TEMPLATES[key]
        for lang in LANGUAGES:
            placeholders = len(set(re.findall(r"\{\{(\d+)\}\}", spec["body"][lang])))
            assert placeholders == len(tpl.params) == len(spec["example"]), (key, lang)
            assert len(tpl.payloads) == len(spec["buttons"][lang]), (key, lang)
        assert all(":" in p for p in tpl.payloads)             # confirm:<id>, oos:<id>, accept:<id> reach handle_button unchanged


def test_template_definitions_are_valid_for_whatsapp():
    for key, spec in TEMPLATES.items():
        assert re.fullmatch(r"[a-z0-9_]+", template_name(key))              # lowercase letters, digits, underscores
        for lang in LANGUAGES:
            assert all(len(b) <= 25 for b in spec["buttons"][lang]) and len(spec["buttons"][lang]) <= 3
            assert "\n" not in spec["body"][lang]


def test_sign_in_code_failure_does_not_break_the_request_or_reveal_the_number(ctx, monkeypatch):
    seller, *_ = seller_flow(ctx)

    def refuse(*a, **k):
        raise WhatsAppError(400, 131047, "closed")
    monkeypatch.setattr(ctx.messenger, "send_text", refuse)
    ctx.auth.request(seller.whatsapp)          # must not raise: the caller always sees the same silent answer
