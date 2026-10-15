"""Cash on delivery is a decision, not an omission: QRIS only in the pilot, said out loud everywhere a buyer or an agent looks,
refused with a readable reason when asked for, and measured (orders that expire unpaid) so the decision can be reopened with data."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from etalase import payment_methods
from etalase.api.main import app as real_app, ctx as real_ctx, limits as real_limits, mcp
from etalase.models import CheckoutError

from .conftest import BUYER, make_product, make_seller
from .test_storefront import shop


@pytest.fixture(scope="module")
def web():
    return TestClient(real_app)


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    real_limits._hits.clear()
    yield
    real_limits._hits.clear()


def test_qris_is_the_only_method_and_cod_gets_an_answer_an_agent_can_repeat():
    assert payment_methods.require("qris") == "qris" and payment_methods.require("") == "qris" and payment_methods.require(" QRIS ") == "qris"
    for ask in ("cod", "COD", "cash_on_delivery", "bayar_di_tempat"):
        with pytest.raises(CheckoutError, match="cash on delivery is not offered"):
            payment_methods.require(ask)
    with pytest.raises(CheckoutError, match="not available; payment is by QRIS"):
        payment_methods.require("bitcoin")
    assert payment_methods.view()["cash_on_delivery"] is False and payment_methods.view()["methods"] == ["qris"]


def test_asking_for_cod_creates_nothing_and_holds_no_stock(ctx, seller, product):
    v = product.variants[0]
    before = ctx.store.get_product(product.id).variant(v.id).stock.reserved
    with pytest.raises(CheckoutError, match="cash on delivery"):
        ctx.checkout.create_checkout(product.id, v.id, 1, BUYER, payment_method="cod")
    assert ctx.store.get_product(product.id).variant(v.id).stock.reserved == before
    assert ctx.store.list_orders(seller.id) == []
    assert ctx.checkout.create_checkout(product.id, v.id, 1, BUYER).payment.method == "qris"


def test_agents_are_told_before_they_try(ctx, seller, product):
    view = ctx.gateway.get_product(product.id)
    assert view["payment"] == {"methods": ["qris"], "cash_on_delivery": False,
                               "note": "Payment is by QRIS, before the seller packs the order. Cash on delivery is not offered."}
    from etalase.gateway.mcp_server import INSTRUCTIONS
    assert "cash on delivery is not offered" in INSTRUCTIONS
    schema = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "create_checkout").inputSchema
    assert schema["properties"]["payment_method"]["default"] == "qris"


def test_mcp_and_rest_refuse_cod_with_the_reason(web):
    seller = make_seller(real_ctx, name="COD Shop", wa="6281200000601")
    product = make_product(real_ctx, seller)
    args = {"product_id": product.id, "variant_id": product.variants[0].id, "quantity": 1, "buyer_name": "Dewi",
            "buyer_phone": "6285700008881", "address_line": "Jl. Dago 10 No 5", "city": "Bandung", "postal_code": "40135",
            "payment_method": "cod"}
    result = json.loads(asyncio.run(mcp.call_tool("create_checkout", args))[0].text)
    assert "cash on delivery is not offered" in result["error"]
    rest = web.post("/agent/v1/checkouts", json={**args, "buyer_phone": "6285700008882"}).json()
    assert "cash on delivery is not offered" in rest["error"]
    assert real_ctx.store.list_orders(seller.id) == []


def test_the_buy_page_says_it_to_people_too(web):
    s, p = shop(web)
    page = web.get(f"/stores/{s['id']}/buy/{p.variants[0].id}").text
    assert "QRIS" in page and "Cash on delivery is not offered" in page


def test_the_operator_report_gives_the_share_of_checkouts_nobody_paid(ctx, seller, product):
    v = product.variants[0].id
    paid = ctx.checkout.create_checkout(product.id, v, 1, BUYER.model_copy(update={"phone": "62857000001"}), channel="web", referrer="direct")
    ctx.checkout.payment_succeeded(paid.payment.reference)
    gone = ctx.checkout.create_checkout(product.id, v, 1, BUYER.model_copy(update={"phone": "62857000002"}), channel="web", referrer="direct")
    ctx.checkout.expire_due(gone.expires_at.replace(year=gone.expires_at.year + 1))
    report = ctx.checkout.attribution_report()
    assert report["totals"] == {"started": 2, "paid": 1, "expired_unpaid": 1, "unpaid_rate": 0.5}
    assert report["by_channel"]["web"] == {"started": 2, "paid": 1, "expired_unpaid": 1}


def test_an_empty_pilot_reports_zero_not_a_division_error(ctx):
    assert ctx.checkout.attribution_report() == {"totals": {"started": 0, "paid": 0, "expired_unpaid": 0, "unpaid_rate": 0.0},
                                                 "by_channel": {}, "by_referrer": {}}
