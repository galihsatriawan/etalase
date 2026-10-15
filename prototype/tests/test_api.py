import asyncio

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app, ctx, mcp

WA = "6287700000001"


@pytest.fixture(scope="module")
def c():
    # The MCP session manager may only start once per process, so the app lifespan runs once.
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_discovery(c):
    assert c.get("/healthz").json()["ok"] is True
    profile = c.get("/.well-known/ucp").json()["ucp"]
    assert profile["services"]["dev.ucp.shopping"][0]["endpoint"].endswith("/ucp/v1")
    assert "dev.ucp.shopping.checkout" in profile["capabilities"]


def test_whatsapp_webhook_verification(c):
    ok = c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "etalase-dev-verify",
                                              "hub.challenge": "42"})
    assert ok.text == "42"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong"}).status_code == 403


def test_whatsapp_cloud_payload_is_parsed_end_to_end(c):
    c.post("/dev/sellers", json={"name": "Webhook Shop", "whatsapp": WA})
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": WA, "type": "text", "text": {"body": "Kaos polos, putih 4, hitam 2, harga 75rb"}}]}}]}]}
    assert c.post("/webhooks/whatsapp", json=payload).status_code == 200
    last = ctx.messenger.outbox[-1]
    assert last.to == WA and "Kaos Polos" in last.text and last.buttons[0][0] == "publish"


def test_connector_requires_key_and_otp_rejects_wrong_code(c):
    assert c.patch("/connector/v1/stock", json=[]).status_code == 401
    assert c.patch("/connector/v1/stock", headers={"X-Etalase-Key": "nope"}, json=[]).status_code == 401
    c.post("/auth/otp", json={"whatsapp": WA})
    assert c.post("/auth/verify", json={"whatsapp": WA, "code": "000000"}).status_code == 401
    assert c.get("/api/me").status_code == 401


def test_mcp_exposes_the_agent_tools():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"search_products", "get_product", "get_shipping_options", "create_checkout", "get_checkout_status"}


def test_openapi_documents_connector_api(c):
    paths = c.get("/openapi.json").json()["paths"]
    assert "/connector/v1/products/{external_id}" in paths and "/connector/v1/stock" in paths


# ---------------------------------------------------------------- security at the HTTP layer
def test_dev_endpoints_need_the_dev_token_when_set(c, monkeypatch):
    monkeypatch.setattr(ctx.settings, "dev_token", "d" * 24)
    assert c.get("/dev/outbox").status_code == 401
    assert c.get("/dev/outbox", headers={"x-dev-token": "d" * 24}).status_code == 200


def test_dev_endpoints_do_not_exist_in_prod(c, monkeypatch):
    monkeypatch.setattr(ctx.settings, "etalase_env", "prod")
    assert c.get("/dev/outbox").status_code == 404


def test_webhooks_without_provider_signatures_need_our_token(c, monkeypatch):
    monkeypatch.setattr(ctx.settings, "webhook_token", "w" * 24)
    body = {"waybill": "NOPE", "status": "delivered"}
    assert c.post("/webhooks/shipping", json=body).status_code == 401
    assert c.post("/webhooks/shipping?token=" + "w" * 24, json=body).status_code == 200
    assert c.post("/webhooks/payment", json={"reference": "x", "status": "paid"}).status_code == 401
    assert c.post("/tasks/expire").status_code == 401


def test_whatsapp_webhook_checks_meta_signature_when_secret_set(c, monkeypatch):
    import hashlib
    import hmac
    import json
    monkeypatch.setattr(ctx.settings, "whatsapp_app_secret", "app-secret")
    raw = json.dumps({"entry": []}).encode()
    assert c.post("/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"}).status_code == 401
    sig = "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    ok = c.post("/webhooks/whatsapp", content=raw, headers={"content-type": "application/json",
                                                             "x-hub-signature-256": sig})
    assert ok.status_code == 200


def test_dashboard_headers_and_media_route(c):
    r = c.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/media/voal-hijab.png").status_code == 200
    assert c.get("/media/..%2F..%2Fetalase%2Fconfig.py").status_code == 404
    assert c.get("/media/.env").status_code == 404


def test_rest_checkout_rejects_oversized_input(c):
    r = c.post("/agent/v1/checkouts", json={"product_id": "x", "variant_id": "y", "quantity": 1,
                                            "buyer_name": "A" * 500, "buyer_phone": "6285700001111",
                                            "address_line": "Jl. Dago 10", "city": "Bandung", "postal_code": "40135"})
    assert r.status_code == 422
