"""Which door an order came through: the channel, and a coarse label for who sent the buyer.

The label is a fixed word or a short slug, never a URL, and a visitor cannot make it anything else: not by a crafted
Referer, a crafted query parameter, a crafted cookie or a field in the agent API's body.
"""

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from etalase import attribution
from etalase.api.main import app as real_app, ctx as real_ctx, limits as real_limits

from .conftest import BUYER
from .test_storefront import form, order_id, post_form, shop
from .test_ucp import API, DEST, HEADERS, fresh_buyer, post


@pytest.fixture(scope="module")
def web():
    return TestClient(real_app)


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """These tests start a handful of web and agent checkouts; the app's limiter is shared by the whole test run, so empty it
    before and after to neither trip nor leave behind a limit for the tests around them."""
    real_limits._hits.clear()
    yield
    real_limits._hits.clear()


# ---------------------------------------------------------------- the labels
@pytest.mark.parametrize("referer,expected", [
    ("https://chatgpt.com/c/abc?x=1", "chatgpt"),
    ("https://chat.openai.com/", "chatgpt"),
    ("https://gemini.google.com/app", "gemini"),
    ("https://claude.ai/chat/1", "claude"),
    ("https://www.perplexity.ai/search/x", "perplexity"),
    ("https://l.instagram.com/?u=https%3A%2F%2Fexample.com", "instagram"),
    ("https://www.google.com/search?q=hijab", "google"),
    ("https://shop.example/page", "other"),
    ("https://evilchatgpt.com/", "other"),                 # a look-alike host is not the real one
    ("https://chatgpt.com.evil.example/", "other"),
    ("", ""), (None, ""), ("not a url", ""), ("javascript:alert(1)", ""),
])
def test_a_referer_becomes_a_known_label_or_other_and_never_a_url(referer, expected):
    assert attribution.from_referer(referer) == expected


def test_our_own_pages_are_not_a_new_source():
    own = ("etalase.example", "toko.etalase.example")
    assert attribution.from_referer("https://toko.etalase.example/stores/x", own) == ""
    assert attribution.from_referer("https://etalase.example/", own) == ""
    assert attribution.from_referer("https://etalase.example.evil.example/", own) == "other"


@pytest.mark.parametrize("params,expected", [
    ({"utm_source": "chatgpt.com"}, "chatgpt"),
    ({"utm_source": "ChatGPT"}, "chatgpt"),
    ({"src": "ig"}, "instagram"),
    ({"src": "wa"}, "whatsapp"),
    ({"src": "ig-bio"}, "ig-bio"),                          # a seller tagging their own link
    ({"src": "<script>alert(1)</script>"}, "script-alert-1-script"),
    ({"src": "x" * 500}, "x" * 40),
    ({"src": ""}, ""), ({}, ""),
])
def test_a_tagged_link_names_its_source_in_a_safe_form(params, expected):
    assert attribution.landing_source(params, None) == expected


def test_a_tag_on_the_link_beats_the_referer_because_apps_often_send_none():
    assert attribution.landing_source({"src": "wa-status"}, "https://l.instagram.com/") == "wa-status"
    assert attribution.landing_source({}, "https://l.instagram.com/") == "instagram"


def test_agents_are_named_by_their_user_agent_product_and_their_ucp_profile_host_only():
    assert attribution.agent_from_user_agent("claude-user/1.0 (+https://claude.ai)") == "claude-user"
    assert attribution.agent_from_user_agent("python-httpx/0.27") == "python-httpx"
    assert attribution.agent_from_user_agent(None) == "" and attribution.agent_from_user_agent("") == ""
    assert attribution.agent_from_ucp_header('profile="https://Platform.Example/ucp/profile?x=secret"') == "platform.example"
    assert attribution.agent_from_ucp_header("garbage") == "" and attribution.agent_from_ucp_header(None) == ""


def test_a_cookie_is_accepted_only_if_it_already_looks_like_one_of_our_labels():
    assert attribution.clean_cookie("chatgpt") == "chatgpt" and attribution.clean_cookie("ig-bio") == "ig-bio"
    for bad in ("", None, "<b>x</b>", "a b", "Chatgpt", "x" * 41, "../../etc"):
        assert attribution.clean_cookie(bad) == ""


# ---------------------------------------------------------------- the store link
def test_a_visit_from_an_assistant_is_remembered_and_lands_on_the_order(web):
    s, p = shop(web)
    v = p.variants[0].id
    web.cookies.clear()
    page = web.get(f"/stores/{s['id']}?utm_source=chatgpt.com")
    assert page.status_code == 200 and "etl_src=chatgpt" in page.headers["set-cookie"]
    assert "HttpOnly" in page.headers["set-cookie"] and "SameSite=lax" in page.headers["set-cookie"]
    assert "Max-Age=1800" in page.headers["set-cookie"]
    web.get(f"/stores/{s['id']}/p/{p.id}")                  # moving on to the product with no signal keeps the label
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    order = real_ctx.store.get_order(oid)
    assert (order.channel, order.referrer) == ("web", "chatgpt")


def test_the_referer_header_alone_is_enough(web):
    s, p = shop(web)
    v = p.variants[0].id
    web.cookies.clear()
    web.get(f"/stores/{s['id']}", headers={"referer": "https://gemini.google.com/app/1"})
    order = real_ctx.store.get_order(order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v))))
    assert order.referrer == "gemini"


def test_a_visit_with_no_signal_is_direct_and_a_forged_cookie_is_ignored(web):
    s, p = shop(web)
    v = p.variants[0].id
    web.cookies.clear()
    page = web.get(f"/stores/{s['id']}")
    assert "set-cookie" not in page.headers                 # nothing to remember, nothing set
    order = real_ctx.store.get_order(order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v))))
    assert (order.channel, order.referrer) == ("web", "direct")
    web.cookies.set("etl_src", "<script>alert(1)</script>")
    order = real_ctx.store.get_order(order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v))))
    assert order.referrer == "direct"
    web.cookies.clear()


def test_moving_between_our_own_pages_does_not_replace_the_source(web):
    s, p = shop(web)
    web.cookies.clear()
    web.get(f"/stores/{s['id']}?src=ig-bio")
    own = web.get(f"/stores/{s['id']}/p/{p.id}", headers={"referer": f"http://testserver/stores/{s['id']}"})
    assert "set-cookie" not in own.headers
    web.cookies.clear()


# ---------------------------------------------------------------- agents
def test_the_agent_api_records_its_own_channel_and_ignores_one_sent_in_the_body(web):
    s, p = shop(web)
    body = {"product_id": p.id, "variant_id": p.variants[0].id, "quantity": 1, "buyer_name": "Dewi",
            "buyer_phone": f"0857{next(itertools.count(70000000))}", "address_line": "Jl. Dago 10 No 5",
            "city": "Bandung", "postal_code": "40135", "channel": "web", "referrer": "chatgpt"}
    r = web.post("/agent/v1/checkouts", json=body, headers={"user-agent": "claude-user/1.0"})
    assert r.status_code == 200, r.text
    order = real_ctx.store.get_order(r.json()["checkout_id"])
    assert (order.channel, order.referrer) == ("rest", "claude-user")


def test_mcp_orders_are_marked_mcp():
    import asyncio
    from etalase.api.main import mcp
    s = real_ctx.store
    from .conftest import make_product, make_seller
    seller = make_seller(real_ctx, name="MCP Attribution Shop", wa="6281200000555")
    product = make_product(real_ctx, seller)
    args = {"product_id": product.id, "variant_id": product.variants[0].id, "quantity": 1, "buyer_name": "Dewi",
            "buyer_phone": "6285700009991", "address_line": "Jl. Dago 10 No 5", "city": "Bandung", "postal_code": "40135"}
    result = asyncio.run(mcp.call_tool("create_checkout", args))
    checkout_id = json.loads(result[0].text)["checkout_id"]
    order = s.get_order(checkout_id)
    assert order.channel == "mcp" and order.referrer == ""        # in-process: no HTTP request, so no User-Agent to name


def test_ucp_orders_carry_the_platform_host_from_the_ucp_agent_header(web):
    from .conftest import make_product, make_seller, variant
    seller = make_seller(real_ctx, name="UCP Attribution Shop", wa="6281200000556")
    product = make_product(real_ctx, seller)
    v = variant(product, "Black")
    created = web.post(f"{API}/checkout-sessions", headers={"UCP-Agent": 'profile="https://agent.example/profile"'},
                       json={"line_items": [{"item": {"id": v.id}, "quantity": 1}]})
    sid = created.json()["id"]
    buyer = fresh_buyer()
    web.put(f"{API}/checkout-sessions/{sid}", headers=HEADERS, json={
        "line_items": [{"id": "li_1", "item": {"id": v.id}, "quantity": 1}], "buyer": buyer,
        "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST]}]}})
    session = web.get(f"{API}/checkout-sessions/{sid}", headers=HEADERS).json()
    option = session["fulfillment"]["methods"][0]["groups"][0]["options"][0]["id"]
    web.put(f"{API}/checkout-sessions/{sid}", headers=HEADERS, json={
        "buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST],
                                                      "groups": [{"selected_option_id": option}]}]}})
    post(web, f"/checkout-sessions/{sid}/complete", {})
    order = real_ctx.store.list_orders(seller.id)[0]
    assert (order.channel, order.referrer) == ("ucp", "agent.example")


# ---------------------------------------------------------------- what we do with it
def test_the_operator_sees_how_many_checkouts_each_door_started_paid_and_lost(web, monkeypatch):
    from .conftest import make_product, make_seller
    monkeypatch.setattr(real_ctx.settings, "operator_token", "o" * 24)
    seller = make_seller(real_ctx, name="Report Shop", wa="6281200000557")
    product = make_product(real_ctx, seller)
    v = product.variants[0].id

    def start(channel, referrer, pay=False):
        o = real_ctx.checkout.create_checkout(product.id, v, 1, BUYER.model_copy(update={"phone": f"62857{next(_report_phones)}"}),
                                              channel=channel, referrer=referrer)
        if pay:
            real_ctx.checkout.payment_succeeded(o.payment.reference)
        return o
    before = web.get("/tasks/attribution", headers={"X-Etalase-Operator-Token": "o" * 24}).json()
    start("web", "report-chatgpt", pay=True)
    gone = start("web", "report-chatgpt")
    real_ctx.checkout.expire_due(gone.expires_at.replace(year=gone.expires_at.year + 1))
    start("mcp", "report-agent", pay=True)
    after = web.get("/tasks/attribution", headers={"X-Etalase-Operator-Token": "o" * 24}).json()

    def delta(table, key):
        zero = {"started": 0, "paid": 0, "expired_unpaid": 0}
        a, b = after[table].get(key, zero), before[table].get(key, zero)
        return {k: a[k] - b[k] for k in zero}
    assert delta("by_referrer", "report-chatgpt") == {"started": 2, "paid": 1, "expired_unpaid": 1}
    assert delta("by_referrer", "report-agent") == {"started": 1, "paid": 1, "expired_unpaid": 0}
    assert delta("by_channel", "mcp")["started"] >= 1 and delta("by_channel", "web")["started"] >= 2
    assert web.get("/tasks/attribution").status_code in (401, 403)           # operator token required


_report_phones = itertools.count(10000001)


def test_a_channel_nobody_defined_is_not_recorded(ctx, seller, product):
    o = ctx.checkout.create_checkout(product.id, product.variants[0].id, 1, BUYER, channel="<script>", referrer="Chat GPT!!")
    assert o.channel == "" and o.referrer == "chat-gpt"
