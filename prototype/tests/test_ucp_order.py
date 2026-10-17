"""UCP Order capability: the order a platform reads, the signed webhooks it receives, and the sandbox-only test mode.

Orders and webhooks are validated against the official SDK models. The platform's side is a fake behind httpx.MockTransport, and the
webhook signatures are verified here with an independent implementation of RFC 9421 and the published key, not with Etalase's own code.
"""

import base64
from datetime import timedelta
import hashlib
import json
import re
from itertools import count

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ucp_sdk.models.schemas.shopping import order as sdk_order
from ucp_sdk.models.schemas.ucp import BusinessSchema

from etalase.config import Settings
from etalase.container import build_context
from etalase.gateway import ucp as ucp_protocol
from etalase.gateway import ucp_order
from etalase.gateway.ucp_signing import Signer, SigningKeyError, content_digest, thumbprint
from etalase.models import CheckoutError, OrderStatus, ReturnReason, now
from etalase.adapters.payment import ManualActionRequired
from etalase.security import InsecureConfig, RateLimiter, validate_settings

from .conftest import BUYER as RETURN_BUYER, make_product, make_seller, variant

API = "/ucp/v1"
PROFILE = "https://platform.example/.well-known/ucp"
HOOK = "https://platform.example/hooks/orders"
PUBLIC_IP = "93.184.216.34"
AGENT = {"UCP-Agent": f'profile="{PROFILE}"'}
SECRET = "simulation-secret-1234"
DEST = {"street_address": "Jl. Dago 10", "address_locality": "Bandung", "address_region": "Jawa Barat",
        "postal_code": "40135", "address_country": "ID"}
_phones = count(6285730000000)
_shops = count(1)
MOCK_CARD = {"payment": {"instruments": [{"id": "instr_1", "handler_id": "mock_payment_handler", "type": "card",
                                          "credential": {"type": "token", "token": "success_token"}}]}}


class FakePlatform:
    """The AI platform: serves its profile (with the webhook URL it declares for orders) and takes the webhooks."""

    def __init__(self, hook=HOOK, answers=(200,)):
        self.hook, self.answers, self.gets, self.posts = hook, list(answers), [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.gets.append(request)
            return httpx.Response(200, json={"ucp": {"version": ucp_protocol.UCP_VERSION, "capabilities": {
                ucp_protocol.ORDER: [{"version": ucp_protocol.UCP_VERSION, "config": {"webhook_url": self.hook}}]}}})
        self.posts.append(request)
        return httpx.Response(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])


class Env:
    def __init__(self, platform: FakePlatform, **settings):
        self.settings = Settings(etalase_env="dev", _env_file=None, **settings)
        self.ctx = build_context(self.settings)
        self.platform = platform
        orders = self.ctx.ucp_orders
        orders.transport = httpx.MockTransport(platform)
        orders.resolver = lambda host, port: [PUBLIC_IP]
        orders.kick = lambda: None                 # deliveries are driven by the test, not by a background thread
        app = FastAPI()
        app.get("/.well-known/ucp")(lambda: ucp_protocol.ucp_profile(self.ctx))
        app.include_router(ucp_protocol.build_router(self.ctx, RateLimiter()))
        if self.settings.ucp_test_mode:
            ucp_order.add_test_routes(app, self.ctx)
        self.client = TestClient(app)
        self.seller = make_seller(self.ctx, name=f"Order Shop {next(_shops)}", wa=f"62813{next(_shops):08d}")
        self.product = make_product(self.ctx, self.seller)
        self.black = variant(self.product, "Black")

    def checkout(self, headers=AGENT, complete_with=None) -> dict:
        """Run a UCP checkout up to and including `complete`; returns the final session (QRIS: requires_escalation)."""
        c, v = self.client, self.black
        s = c.post(f"{API}/checkout-sessions", headers=headers, json={"line_items": [{"item": {"id": v.id}, "quantity": 1}]}).json()
        buyer = {"first_name": "Dewi", "last_name": "Lestari", "phone_number": str(next(_phones))}
        url = f"{API}/checkout-sessions/{s['id']}"
        s = c.put(url, headers=headers, json={"buyer": buyer, "fulfillment": {"methods": [{"type": "shipping", "destinations": [DEST]}]}}).json()
        option = s["fulfillment"]["methods"][0]["groups"][0]["options"][0]["id"]
        c.put(url, headers=headers, json={"buyer": buyer, "fulfillment": {"methods": [
            {"type": "shipping", "destinations": [DEST], "groups": [{"selected_option_id": option}]}]}})
        done = c.post(f"{url}/complete", headers=headers, json=complete_with or {"payment": {"instruments": []}})
        assert done.status_code == 200, done.text
        return done.json()

    def order_of(self, session: dict):
        return self.ctx.store.get_order(self.ctx.store.get_doc(ucp_protocol.SESSION_KIND, session["id"])["order_id"])

    def pay(self, session: dict):
        order = self.order_of(session)
        self.ctx.checkout.payment_succeeded(order.payment.reference)
        return self.ctx.store.get_order(order.id)

    def read(self, order_id: str, headers=AGENT):
        return self.client.get(f"{API}/orders/{order_id}", headers=headers)

    def hooks(self) -> list[dict]:
        return [json.loads(r.content) for r in self.platform.posts]


@pytest.fixture
def env():
    return Env(FakePlatform(), ucp_platforms=json.dumps([PROFILE]))


def paid(env: Env):
    session = env.checkout()
    return session, env.pay(session)


# ------------------------------------------------------------------ an independent RFC 9421 verifier
def jwk_public_key(jwk: dict):
    b = lambda v: int.from_bytes(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)), "big")
    if jwk["kty"] == "OKP":
        return ed25519.Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4)))
    return ec.EllipticCurvePublicNumbers(b(jwk["x"]), b(jwk["y"]), ec.SECP256R1()).public_key()


def verify_signature(request: httpx.Request, jwk: dict) -> list[str]:
    """Rebuild the signature base from the request as received and check it; returns the covered components."""
    m = re.fullmatch(r'sig1=(\(([^)]*)\);created=(\d+);keyid="([^"]+)")', request.headers["signature-input"])
    assert m, request.headers["signature-input"]
    params, comps, keyid = m.group(1), re.findall(r'"([^"]+)"', m.group(2)), m.group(4)
    assert keyid == jwk["kid"]
    host = request.headers["host"].lower()
    values = {"@method": request.method, "@authority": host, "@path": request.url.path, "@query": "?" + request.url.query.decode()}
    lines = [f'"{c}": {values[c] if c in values else request.headers[c]}' for c in comps] + [f'"@signature-params": {params}']
    sig = re.fullmatch(r"sig1=:([A-Za-z0-9+/=]+):", request.headers["signature"])
    assert sig
    raw = base64.b64decode(sig.group(1))
    key = jwk_public_key(jwk)
    if jwk["kty"] == "OKP":
        key.verify(raw, "\n".join(lines).encode())
    else:
        assert len(raw) == 64                      # fixed-width r||s, not DER
        key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   "\n".join(lines).encode(), ec.ECDSA(hashes.SHA256()))
    return comps


def pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


# ------------------------------------------------------------------ the profile and the signing key
def test_the_profile_advertises_the_order_capability_and_publishes_the_signing_key(env):
    profile = env.client.get("/.well-known/ucp").json()
    BusinessSchema(**profile["ucp"])
    cap = profile["ucp"]["capabilities"][ucp_protocol.ORDER][0]
    assert cap["spec"].endswith("/specification/order") and cap["schema"].endswith("/schemas/shopping/order.json")
    [jwk] = profile["signing_keys"]
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256" and jwk["alg"] == "ES256" and jwk["use"] == "sig" and "d" not in jwk
    assert jwk["kid"] == thumbprint(jwk_public_key(jwk))


def test_a_configured_key_is_published_under_the_same_kid_every_time():
    key = ec.generate_private_key(ec.SECP256R1())
    a, b = Signer.load(pem(key)), Signer.load(pem(key))
    assert a.kid == b.kid == thumbprint(key.public_key()) and a.configured
    assert Signer.load("").configured is False and Signer.load("").kid != a.kid      # the throwaway key is not a configured one


@pytest.mark.parametrize("key,why", [(ec.generate_private_key(ec.SECP384R1()), "P-256"), (rsa.generate_private_key(65537, 2048), "RSA")])
def test_a_key_that_is_not_p256_or_ed25519_is_refused(key, why):
    with pytest.raises(SigningKeyError, match="P-256 or Ed25519"):
        Signer.load(pem(key))


def test_garbage_is_not_a_signing_key():
    with pytest.raises(SigningKeyError):
        Signer.load("not a key")


@pytest.mark.parametrize("key", [ec.generate_private_key(ec.SECP256R1()), ed25519.Ed25519PrivateKey.generate()], ids=["ES256", "Ed25519"])
def test_signatures_verify_with_the_published_key_and_cover_what_ucp_requires(key):
    signer = Signer.load(pem(key))
    body = b'{"id":"ord_1"}'
    url = "https://platform.example/hooks/orders?x=1"
    headers = {"Idempotency-Key": "k1", "UCP-Agent": 'profile="https://shop.example/.well-known/ucp"', "Webhook-Id": "w1", "Webhook-Timestamp": "1760000000"}
    added = signer.sign("POST", url, headers, body, created=1760000000)
    assert added["Content-Digest"] == content_digest(body) == "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    request = httpx.Request("POST", url, headers={**headers, **added, "Host": "platform.example"}, content=body)
    comps = verify_signature(request, signer.jwk())
    assert comps == ["@method", "@authority", "@path", "@query", "content-digest", "content-type", "idempotency-key", "ucp-agent",
                     "webhook-id", "webhook-timestamp"]
    with pytest.raises(Exception):         # the signature binds the host it was made for
        verify_signature(httpx.Request("POST", url, headers={**headers, **added, "Host": "other.example"}, content=body), signer.jwk())


def test_an_order_webhook_signed_for_another_header_value_does_not_verify():
    signer = Signer.load("")
    headers = {"Webhook-Id": "w1", "Webhook-Timestamp": "1760000000", "UCP-Agent": 'profile="https://a.example/.well-known/ucp"'}
    added = signer.sign("POST", "https://platform.example/h", headers, b"{}")
    headers["Webhook-Id"] = "w2"                                                         # changed in transit
    with pytest.raises(Exception):
        verify_signature(httpx.Request("POST", "https://platform.example/h", headers={**headers, **added, "Host": "platform.example"}, content=b"{}"), signer.jwk())


# ------------------------------------------------------------------ reading the order
def test_there_is_no_order_until_the_sale_is_paid_then_it_is_a_valid_ucp_order(env):
    session = env.checkout()
    order_id = env.order_of(session).id
    assert session["status"] == "requires_escalation" and "order" not in session
    assert env.read(order_id).status_code == 404
    env.pay(session)
    r = env.read(order_id)
    assert r.status_code == 200
    sdk_order.Order.model_validate(r.json())
    o = r.json()
    assert o["id"] == order_id and o["checkout_id"] == session["id"] and o["currency"] == "IDR"
    assert o["permalink_url"].endswith(f"/pay/{order_id}")
    [line] = o["line_items"]
    assert line["id"] == "li_1" and line["quantity"] == {"original": 1, "total": 1, "fulfilled": 0} and line["status"] == "processing"
    assert line["item"]["id"] == env.black.id and line["item"]["price"] == 85000 * 100
    assert [t["type"] for t in o["totals"]] == ["subtotal", "fulfillment", "total"] and o["totals"][-1]["amount"] == (85000 + 12000) * 100
    [exp] = o["fulfillment"]["expectations"]
    assert exp["method_type"] == "shipping" and exp["line_items"] == [{"id": "li_1", "quantity": 1}]
    assert exp["destination"]["postal_code"] == "40135" and exp["destination"]["address_locality"] == "Bandung" and "id" not in exp["destination"]
    option = session["fulfillment"]["methods"][0]["groups"][0]["options"][0]
    assert exp["description"] == option["title"]                    # what the buyer was told at checkout is what the order says
    assert "adjustments" not in o and "events" not in o["fulfillment"]
    assert "name" not in json.dumps(o["fulfillment"]) and "6285" not in json.dumps(o)         # no buyer name or phone in the order


def test_only_the_platform_that_started_the_checkout_can_read_its_order(env):
    session, order = paid(env)
    assert env.read(order.id).status_code == 200
    assert env.read(order.id, headers={"UCP-Agent": 'profile="https://other.example/.well-known/ucp"'}).status_code == 404
    assert env.read("ord_doesnotexist").status_code == 404
    assert env.client.get(f"{API}/orders/{order.id}").status_code == 400                       # UCP-Agent is required
    assert env.read(order.id, headers={"UCP-Agent": 'profile="' + "x" * 400 + '"'}).status_code == 404


def test_an_order_placed_without_the_ucp_channel_is_not_a_ucp_order(env):
    other = env.ctx.checkout.create_checkout(env.product.id, env.black.id, 1, RETURN_BUYER)
    env.ctx.checkout.payment_succeeded(other.payment.reference)
    assert env.read(other.id).status_code == 404 and env.platform.posts == []


def test_the_order_follows_accept_ship_and_delivery_with_stable_append_only_events(env):
    session, order = paid(env)
    env.ctx.checkout.seller_accepts(env.seller.id, order.id)
    env.ctx.checkout.mark_shipped(env.seller.id, order.id, "JNE777000111")
    shipped = env.read(order.id).json()
    sdk_order.Order.model_validate(shipped)
    events = {e["type"]: e for e in shipped["fulfillment"]["events"]}
    assert list(events) == ["processing", "shipped"]
    assert events["shipped"]["tracking_number"] == "JNE777000111" and events["shipped"]["tracking_url"].endswith(f"/pay/{order.id}")
    assert events["shipped"]["carrier"] and events["shipped"]["line_items"] == [{"id": "li_1", "quantity": 1}]
    assert shipped["line_items"][0]["quantity"]["fulfilled"] == 1 and shipped["line_items"][0]["status"] == "fulfilled"
    env.ctx.checkout.shipment_update("JNE777000111", "delivered")
    delivered = env.read(order.id).json()
    sdk_order.Order.model_validate(delivered)
    assert [e["type"] for e in delivered["fulfillment"]["events"]] == ["processing", "shipped", "delivered"]
    for before in shipped["fulfillment"]["events"]:                          # what was said stays said: same id, same time
        assert before in delivered["fulfillment"]["events"]
    assert env.read(order.id).json() == delivered                            # reading changes nothing


def test_a_parcel_that_comes_back_or_is_lost_is_told_to_the_platform(env):
    for outcome, event in (("returned", "returned_to_sender"), ("failed", "undeliverable")):
        session, order = paid(env)
        env.ctx.checkout.seller_accepts(env.seller.id, order.id)
        waybill = f"JNE{outcome[:3].upper()}99{len(env.platform.posts):04d}"
        env.ctx.checkout.mark_shipped(env.seller.id, order.id, waybill)
        env.ctx.checkout.shipment_update(waybill, outcome)
        types = [e["type"] for e in env.read(order.id).json()["fulfillment"]["events"]]
        assert types[-1] == event, types


def test_a_paid_order_the_seller_rejects_is_cancelled_with_a_refund_adjustment(env):
    session, order = paid(env)
    env.ctx.checkout.seller_rejects_paid(env.seller.id, order.id)
    o = env.read(order.id).json()
    sdk_order.Order.model_validate(o)
    [adj] = o["adjustments"]
    assert adj["type"] == "cancellation" and adj["status"] == "completed"
    assert adj["totals"] == [{"type": "total", "amount": -(85000 + 12000) * 100}]          # negative: money back to the buyer
    assert o["line_items"][0]["quantity"]["total"] == 0 and o["line_items"][0]["status"] == "removed"


def test_a_refund_the_provider_cannot_make_yet_is_pending_until_it_is_done(env, monkeypatch):
    def cannot(order, amount=None):
        raise ManualActionRequired("refund by hand")
    monkeypatch.setattr(env.ctx.payment, "refund", cannot)
    session, order = paid(env)
    env.ctx.checkout.seller_rejects_paid(env.seller.id, order.id)
    [adj] = env.read(order.id).json()["adjustments"]
    assert adj["type"] == "cancellation" and adj["status"] == "pending" and adj["totals"][0]["amount"] == -(85000 + 12000) * 100


def test_a_return_is_an_adjustment_that_ends_completed_when_the_buyer_is_refunded(env):
    session, order = paid(env)
    phone = env.ctx.store.get_order(order.id).buyer.phone
    env.ctx.checkout.seller_accepts(env.seller.id, order.id)
    env.ctx.checkout.mark_shipped(env.seller.id, order.id, "JNE555000222")
    env.ctx.checkout.shipment_update("JNE555000222", "delivered")
    env.ctx.returns.request(order.id, phone, ReturnReason.DAMAGED.value, "cracked")
    o = env.read(order.id).json()
    sdk_order.Order.model_validate(o)
    [ret] = o["adjustments"]
    assert ret["type"] == "return" and ret["status"] == "pending" and "damaged" in ret["description"] and "cracked" not in json.dumps(o)
    env.ctx.returns.seller_responds(env.seller.id, order.id, True)
    env.ctx.returns.add_return_tracking(order.id, phone, "JNE666000333")
    env.ctx.returns.seller_received(env.seller.id, order.id)
    o = env.read(order.id).json()
    sdk_order.Order.model_validate(o)
    kinds = {a["type"]: a for a in o["adjustments"]}
    assert kinds["return"]["status"] == "completed" and kinds["refund"]["status"] == "completed" and kinds["refund"]["totals"][0]["amount"] < 0


# ------------------------------------------------------------------ webhooks
def test_a_paid_order_is_sent_once_to_an_allowed_platform_as_a_signed_full_order(env):
    session, order = paid(env)
    assert env.platform.posts == [] and env.ctx.ucp_orders.pending() == 1             # stored first, sent by the delivery job
    assert env.ctx.ucp_orders.deliver_due() and env.ctx.ucp_orders.pending() == 0
    [request] = env.platform.posts
    assert request.headers["host"] == "platform.example" and request.url.host == PUBLIC_IP       # connected to the checked address, named for the platform
    assert request.url.path == "/hooks/orders"
    body = json.loads(request.content)
    sdk_order.Order.model_validate(body)
    assert body == env.read(order.id).json()                                          # the same current-state snapshot as Get Order
    assert request.headers["webhook-id"] and re.fullmatch(r"\d{10}", request.headers["webhook-timestamp"])
    assert request.headers["idempotency-key"] == request.headers["webhook-id"]
    assert request.headers["ucp-agent"] == 'profile="http://localhost:8080/.well-known/ucp"'
    assert request.headers["content-digest"] == content_digest(request.content)
    jwk = env.client.get("/.well-known/ucp").json()["signing_keys"][0]
    comps = verify_signature(request, jwk)
    assert {"@method", "@authority", "@path", "content-digest", "content-type", "ucp-agent", "webhook-id", "webhook-timestamp"} <= set(comps)
    assert env.ctx.ucp_orders.deliver_due() == [] and len(env.platform.posts) == 1       # delivered once, not again
    assert env.read(order.id).status_code == 200 and len(env.platform.posts) == 1        # reading does not announce anything


def test_every_change_is_announced_with_the_whole_order_and_a_new_event_id(env):
    session, order = paid(env)
    env.ctx.checkout.seller_accepts(env.seller.id, order.id)
    env.ctx.checkout.mark_shipped(env.seller.id, order.id, "JNE111000999")
    env.ctx.checkout.shipment_update("JNE111000999", "delivered")
    sent = env.ctx.ucp_orders.deliver_due()
    assert len(sent) == 4
    bodies = env.hooks()
    assert [len(b["fulfillment"].get("events", [])) for b in bodies] == [0, 1, 2, 3]
    assert all(b["id"] == order.id and b["line_items"] and b["checkout_id"] == session["id"] for b in bodies)      # never a delta
    assert len({r.headers["webhook-id"] for r in env.platform.posts}) == 4
    for b in bodies:
        sdk_order.Order.model_validate(b)


def test_a_change_the_platform_cannot_see_sends_nothing(env):
    session, order = paid(env)
    env.ctx.ucp_orders.deliver_due()
    env.ctx.ucp_orders.sync(order.id)
    env.ctx.events.publish("order.paid", {"order_id": order.id})              # a repeated event
    assert env.ctx.ucp_orders.pending() == 0 and len(env.platform.posts) == 1


def test_a_platform_that_is_not_on_the_list_gets_no_webhook_and_its_profile_is_never_fetched():
    env = Env(FakePlatform(), ucp_platforms=json.dumps(["https://someone-else.example/.well-known/ucp"]))
    session, order = paid(env)
    assert env.platform.gets == [] and env.ctx.ucp_orders.pending() == 0 and env.platform.posts == []
    assert env.read(order.id).status_code == 200                              # it can still read its own order


def test_with_an_empty_allow_list_nobody_is_called():
    env = Env(FakePlatform())
    paid(env)
    assert env.platform.gets == [] and env.platform.posts == [] and env.ctx.ucp_orders.pending() == 0


def test_a_webhook_url_on_another_host_is_ignored_unless_the_list_names_that_host():
    other = "https://hooks.elsewhere.example/orders"
    env = Env(FakePlatform(hook=other), ucp_platforms=json.dumps([PROFILE]))
    paid(env)
    assert env.ctx.ucp_orders.pending() == 0                                   # a profile cannot send our webhooks wherever it likes
    env = Env(FakePlatform(hook=other), ucp_platforms=json.dumps([{"profile": PROFILE, "webhook_hosts": ["hooks.elsewhere.example"]}]))
    paid(env)
    assert env.ctx.ucp_orders.pending() == 1


@pytest.mark.parametrize("hook", ["http://platform.example/hooks", "https://user:pw@platform.example/hooks", "ftp://platform.example/x",
                                  "https://platform.example/" + "x" * 500])
def test_a_webhook_url_that_is_not_plain_https_is_ignored(hook):
    env = Env(FakePlatform(hook=hook), ucp_platforms=json.dumps([PROFILE]))
    paid(env)
    assert env.ctx.ucp_orders.pending() == 0


def test_a_failed_delivery_is_retried_as_the_same_event(env):
    env.platform.answers[:] = [500, 503, 200]
    session, order = paid(env)
    orders = env.ctx.ucp_orders
    assert orders.deliver_due() == []                                          # attempt 1: 500
    assert orders.deliver_due() == []                                          # not due yet: nothing is sent early
    assert len(env.platform.posts) == 1
    later = now() + timedelta(seconds=3)
    assert orders.deliver_due(later) == []                                     # attempt 2: 503
    assert orders.deliver_due(later + timedelta(seconds=11)) != []   # attempt 3: 200
    first, second, third = env.platform.posts
    assert {r.headers["webhook-id"] for r in env.platform.posts} == {first.headers["webhook-id"]}
    assert {r.headers["webhook-timestamp"] for r in env.platform.posts} == {first.headers["webhook-timestamp"]}
    assert first.content == second.content == third.content                    # the same full order
    assert len({r.headers["signature"] for r in env.platform.posts}) == 3      # signed again each time: `created` is the send time
    assert orders.pending() == 0


def test_a_4xx_stops_the_retries_except_when_the_platform_only_asks_to_wait(env):
    env.platform.answers[:] = [400]
    session, order = paid(env)
    assert env.ctx.ucp_orders.deliver_due() == [] and env.ctx.ucp_orders.pending() == 0 and len(env.platform.posts) == 1
    assert ("ops.manual_action", {"order_id": order.id, "action": "ucp_webhook"}) in env.ctx.events.published
    env = Env(FakePlatform(answers=[429, 200]), ucp_platforms=json.dumps([PROFILE]))
    paid(env)
    assert env.ctx.ucp_orders.deliver_due() == [] and env.ctx.ucp_orders.pending() == 1       # 429: try again later
    assert env.ctx.ucp_orders.deliver_due(now() + timedelta(seconds=3)) != []


def test_a_delivery_that_never_works_is_given_up_after_the_last_retry_and_an_operator_is_told(env):
    env.platform.answers[:] = [500]
    session, order = paid(env)
    orders, when = env.ctx.ucp_orders, now()
    for n, wait in enumerate(ucp_order.RETRY_AFTER):
        orders.deliver_due(when)
        assert orders.pending() == 1 and len(env.platform.posts) == n + 1
        when += timedelta(seconds=wait + 1)
    orders.deliver_due(when)
    assert orders.pending() == 0 and len(env.platform.posts) == len(ucp_order.RETRY_AFTER) + 1
    assert ("ops.manual_action", {"order_id": order.id, "action": "ucp_webhook"}) in env.ctx.events.published
    assert orders.deliver_due(when + timedelta(days=30)) == [] and len(env.platform.posts) == len(ucp_order.RETRY_AFTER) + 1


def test_deliveries_of_one_order_go_out_in_order_even_when_the_first_is_being_retried(env):
    env.platform.answers[:] = [500, 200]
    session, order = paid(env)
    env.ctx.checkout.seller_accepts(env.seller.id, order.id)                  # a second event, queued behind the first
    orders = env.ctx.ucp_orders
    assert orders.deliver_due() == [] and len(env.platform.posts) == 1          # the first fails; the second must wait for it
    assert len(orders.deliver_due(now() + timedelta(seconds=3))) == 2
    assert [len(json.loads(r.content)["fulfillment"].get("events", [])) for r in env.platform.posts] == [0, 0, 1]


def test_a_redirect_is_not_followed_and_ends_the_delivery(env):
    env.platform.answers[:] = [302]
    paid(env)
    assert env.ctx.ucp_orders.deliver_due() == [] and env.ctx.ucp_orders.pending() == 0 and len(env.platform.posts) == 1


def test_a_second_worker_cannot_send_the_same_delivery_at_the_same_time(env):
    paid(env)
    orders = env.ctx.ucp_orders
    [delivery_id] = env.ctx.store.get_doc(ucp_order.OUTBOX_KIND, ucp_order.OUTBOX_ID)["ids"]
    assert orders._attempt(delivery_id, now()) == "delivered"
    assert orders._attempt(delivery_id, now()) == "skipped" and len(env.platform.posts) == 1


def test_the_real_delivery_thread_sends_without_the_test_driving_it():
    import time
    platform = FakePlatform()
    env = Env(platform, ucp_platforms=json.dumps([PROFILE]))
    del env.ctx.ucp_orders.kick                                                # the real one, which sends off the caller's thread
    paid(env)
    for _ in range(50):
        if platform.posts:
            break
        time.sleep(0.1)
    assert len(platform.posts) == 1


# ------------------------------------------------------------------ connections are pinned to a checked address
def test_a_profile_or_webhook_that_resolves_to_an_internal_address_is_never_contacted():
    for bad in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "192.168.1.1"):
        platform = FakePlatform()
        env = Env(platform, ucp_platforms=json.dumps([PROFILE]))
        env.ctx.ucp_orders.resolver = lambda host, port, bad=bad: [PUBLIC_IP, bad]         # one bad answer is enough
        paid(env)
        assert platform.gets == [] and platform.posts == [] and env.ctx.ucp_orders.pending() == 0, bad


def test_a_name_that_does_not_resolve_is_just_a_failed_delivery_not_a_crash(env):
    def broken(host, port):
        raise ucp_order.DeliveryError("does not resolve")
    session, order = paid(env)
    env.ctx.ucp_orders.resolver = broken
    assert env.ctx.ucp_orders.deliver_due() == [] and env.ctx.ucp_orders.pending() == 1


def test_the_profile_is_fetched_once_and_reused(env):
    paid(env)
    paid(env)
    assert len(env.platform.gets) == 1
    assert env.platform.gets[0].url.host == PUBLIC_IP and env.platform.gets[0].headers["host"] == "platform.example"


def test_an_oversized_profile_is_not_used():
    class Huge(FakePlatform):
        def __call__(self, request):
            return httpx.Response(200, content=b"{" + b" " * 300_000 + b"}") if request.method == "GET" else super().__call__(request)
    env = Env(Huge(), ucp_platforms=json.dumps([PROFILE]))
    paid(env)
    assert env.ctx.ucp_orders.pending() == 0


# ------------------------------------------------------------------ the allow-list and startup checks
def test_the_allow_list_accepts_urls_and_objects_and_refuses_anything_else():
    got = ucp_order.parse_platforms(json.dumps([PROFILE, {"profile": "https://b.example/p", "webhook_hosts": ["H.b.example"]}]))
    assert [p.profile for p in got] == [PROFILE, "https://b.example/p"] and got[1].webhook_hosts == {"h.b.example"}
    assert ucp_order.parse_platforms("") == [] and ucp_order.parse_platforms("  ") == []
    for bad in ("not json", '{"a": 1}', '["ftp://x.example"]', '["https://u:p@x.example/"]', '[{"profile": 3}]',
                '[{"profile": "https://x.example/", "webhook_hosts": "h"}]', "[7]"):
        with pytest.raises(ValueError):
            ucp_order.parse_platforms(bad)


def settings(**kw):
    return Settings(_env_file=None, **kw)


MOCKS = {"payment": "mock", "shipping": "mock", "messaging": "console"}


def test_startup_refuses_test_mode_outside_dev_or_with_real_adapters_or_without_a_secret():
    validate_settings(settings(ucp_test_mode=True, ucp_simulation_secret=SECRET), MOCKS)
    for kw, modes, why in ((dict(etalase_env="prod"), MOCKS, "only runs with ETALASE_ENV=dev"),
                           (dict(), {**MOCKS, "payment": "xendit"}, "mock payment"),
                           (dict(), {**MOCKS, "shipping": "biteship"}, "mock shipping"),
                           (dict(), {**MOCKS, "messaging": "whatsapp"}, "console messenger"),
                           (dict(ucp_simulation_secret="short"), MOCKS, "UCP_SIMULATION_SECRET")):
        with pytest.raises(InsecureConfig, match=why):
            validate_settings(settings(**{"ucp_test_mode": True, "ucp_simulation_secret": SECRET, **kw}), modes)


def test_startup_wants_a_real_signing_key_outside_dev_once_platforms_are_listed_and_checks_the_list():
    prod = dict(etalase_env="prod", session_secret="s" * 40, webhook_token="w" * 20, whatsapp_token="t", whatsapp_phone_number_id="1",
                whatsapp_app_secret="a", whatsapp_verify_token="a-changed-token")
    modes = {"payment": "xendit", "shipping": "biteship", "messaging": "whatsapp"}
    validate_settings(settings(**prod), modes)
    with pytest.raises(InsecureConfig, match="UCP_SIGNING_KEY is required"):
        validate_settings(settings(**prod, ucp_platforms=json.dumps([PROFILE])), modes)
    validate_settings(settings(**prod, ucp_platforms=json.dumps([PROFILE]), ucp_signing_key=pem(ec.generate_private_key(ec.SECP256R1()))), modes)
    with pytest.raises(InsecureConfig, match="must be https"):
        validate_settings(settings(**prod, ucp_platforms='["http://p.example/x"]', ucp_signing_key=pem(ed25519.Ed25519PrivateKey.generate())), modes)
    with pytest.raises(InsecureConfig, match="UCP_SIGNING_KEY"):
        validate_settings(settings(ucp_signing_key="nonsense"), MOCKS)
    with pytest.raises(InsecureConfig, match="UCP_PLATFORMS"):
        validate_settings(settings(ucp_platforms="nonsense"), MOCKS)
    validate_settings(settings(ucp_platforms=json.dumps([PROFILE])), MOCKS)           # dev: a throwaway key is fine


def test_without_a_real_key_the_profile_publishes_none_outside_dev():
    prod = build_context(Settings(etalase_env="dev", _env_file=None))
    assert prod.ucp_orders.signing_keys() and not prod.ucp_orders.signer.configured    # dev: the throwaway key
    prod.settings.etalase_env = "prod"
    assert prod.ucp_orders.signing_keys() == []                                         # prod without a configured key: nothing is signed, nothing published


# ------------------------------------------------------------------ test mode (sandbox only)
@pytest.fixture
def lab():
    return Env(FakePlatform(hook="http://localhost:8284/webhooks/order"), ucp_test_mode=True, ucp_simulation_secret=SECRET,
               ucp_platforms=json.dumps(["http://localhost:8285/profiles/agent.json"]))


LAB_AGENT = {"UCP-Agent": 'profile="http://localhost:8285/profiles/agent.json"'}


def test_the_mock_card_token_is_money_only_in_test_mode(env, lab):
    plain = env.checkout(complete_with=MOCK_CARD)
    assert plain["status"] == "requires_escalation" and "order" not in plain                 # production: a card token pays nothing
    assert env.order_of(plain).status == OrderStatus.AWAITING_PAYMENT
    done = lab.checkout(headers=LAB_AGENT, complete_with=MOCK_CARD)
    assert done["status"] == "completed" and done["order"]["id"] == lab.order_of(done).id
    assert lab.order_of(done).status == OrderStatus.PAID
    wrong = lab.checkout(headers=LAB_AGENT, complete_with={"payment": {"instruments": [
        {"handler_id": "mock_payment_handler", "credential": {"type": "token", "token": "fail_token"}}]}})
    assert wrong["status"] == "ready_for_complete" and "order" not in wrong                  # a declined card: nothing is reserved, nothing is created
    assert [m["code"] for m in wrong["messages"] if m["type"] == "error"] == ["payment_failed"]
    assert wrong["messages"][-1]["severity"] == "recoverable"
    assert [o.id for o in lab.ctx.store.list_orders(lab.seller.id)] == [lab.order_of(done).id]   # only the paid order exists
    other = lab.checkout(headers=LAB_AGENT, complete_with={"payment": {"instruments": [
        {"handler_id": "some_real_handler", "credential": {"type": "token", "token": "success_token"}}]}})
    assert other["status"] == "requires_escalation"


def test_in_test_mode_the_localhost_platform_receives_the_created_event_and_a_single_shipping_update(lab):
    done = lab.checkout(headers=LAB_AGENT, complete_with=MOCK_CARD)
    order_id = done["order"]["id"]
    assert lab.ctx.ucp_orders.deliver_due()
    assert lab.client.post(f"/testing/simulate-shipping/{order_id}").status_code == 403
    assert lab.client.post(f"/testing/simulate-shipping/{order_id}", headers={"Simulation-Secret": "wrong"}).status_code == 403
    assert lab.client.post(f"/testing/simulate-shipping/ord_unknown", headers={"Simulation-Secret": SECRET}).status_code == 404
    shipped = lab.client.post(f"/testing/simulate-shipping/{order_id}", headers={"Simulation-Secret": SECRET})
    assert shipped.status_code == 200 and shipped.json() == {"status": "shipped"}
    lab.ctx.ucp_orders.deliver_due()
    created, update = lab.hooks()
    assert "events" not in created["fulfillment"]
    assert [e["type"] for e in update["fulfillment"]["events"]] == ["processing", "shipped"]    # accept and ship are one announced step
    assert update["fulfillment"]["events"][1]["tracking_number"].startswith("SIM")
    assert lab.platform.posts[0].url.host == "localhost"                                          # test mode talks to the local mock platform


def test_put_orders_adds_events_and_adjustments_only_in_test_mode_and_checks_them(env, lab):
    done = lab.checkout(headers=LAB_AGENT, complete_with=MOCK_CARD)
    order_id = done["order"]["id"]
    current = lab.read(order_id, headers=LAB_AGENT).json()
    event = {"id": "evt_x1", "type": "shipped", "occurred_at": now().isoformat(), "tracking_number": "TRACK123",
             "tracking_url": "http://track.me/123", "line_items": [{"id": "li_1", "quantity": 1}], "description": "Shipped via FedEx"}
    current["fulfillment"]["events"] = [event]
    put = lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json=current)
    assert put.status_code == 200
    sdk_order.Order.model_validate(put.json())
    assert put.json()["fulfillment"]["events"][0]["tracking_number"] == "TRACK123"
    adj = {"id": "adj_x1", "type": "refund", "occurred_at": now().isoformat(), "status": "pending",
           "totals": [{"type": "total", "amount": -500}], "description": "Customer refund request"}
    again = lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json={**put.json(), "adjustments": [adj]})
    assert again.status_code == 200 and again.json()["adjustments"][0]["totals"][0]["amount"] == -500
    assert lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json={**put.json(), "adjustments": [adj]}).json() == again.json()     # same ids: nothing doubles
    bad = {**adj, "id": "adj_x2", "status": "INVALID_STATUS"}
    assert lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json={"adjustments": [bad]}).status_code == 422
    assert lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json={"adjustments": [{"id": "adj_y"}]}).status_code == 422
    assert lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json=[1]).status_code == 400
    assert lab.client.put(f"{API}/orders/{order_id}", headers=AGENT, json=current).status_code == 404       # another platform's order
    lab.ctx.ucp_orders.deliver_due()
    assert len(lab.platform.posts) >= 3 and all("fulfillment" in b for b in lab.hooks())
    plain_done = env.checkout()
    env.pay(plain_done)
    assert env.client.put(f"{API}/orders/{env.order_of(plain_done).id}", headers=AGENT, json={}).status_code in (404, 405)    # production: no PUT
    assert env.client.post(f"/testing/simulate-shipping/{env.order_of(plain_done).id}", headers={"Simulation-Secret": SECRET}).status_code == 404


def test_the_suite_style_update_cannot_remove_what_was_said(lab):
    done = lab.checkout(headers=LAB_AGENT, complete_with=MOCK_CARD)
    order_id = done["order"]["id"]
    lab.client.post(f"/testing/simulate-shipping/{order_id}", headers={"Simulation-Secret": SECRET})
    before = lab.read(order_id, headers=LAB_AGENT).json()
    after = lab.client.put(f"{API}/orders/{order_id}", headers=LAB_AGENT, json={"fulfillment": {"events": []}}).json()
    assert after["fulfillment"]["events"] == before["fulfillment"]["events"]
