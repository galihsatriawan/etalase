"""The checks that run before anything costly: who is calling, and how often.

The MCP endpoint is the public front door for buyers' AI agents. A shipping quote costs a courier-API call and a checkout
holds stock for 15 minutes, so those tools carry the same per-client limits as the REST routes. On the seller side,
Gemini calls are budgeted per seller.
"""

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from etalase.api import main as api
from etalase.config import Settings
from etalase.container import build_context
from etalase.gateway.mcp_server import build_mcp
from etalase.security import RateLimiter
from tests.conftest import make_seller

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
QUOTE = {"product_id": "p", "variant_id": "v", "quantity": 1, "postal_code": "40115"}


def call(client, tool, args, ip):
    r = client.post("/mcp/", headers={**HEADERS, "x-forwarded-for": ip},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert r.status_code == 200
    return json.loads(r.json()["result"]["content"][0]["text"])


def mcp_app(limits):
    """The MCP server on its own small app. The real app's session manager can only be started once per process."""
    ctx = build_context(Settings(etalase_env="dev", _env_file=None))
    server = build_mcp(ctx, limits)

    @asynccontextmanager
    async def lifespan(_app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", server.streamable_http_app())
    return app


def test_mcp_tools_are_rate_limited_per_client_and_share_buckets_with_rest():
    limits = RateLimiter()
    with TestClient(mcp_app(limits)) as client:
        answers = [call(client, "get_shipping_options", QUOTE, "203.0.113.7") for _ in range(61)]
        assert all(a.get("error") != "rate_limited" for a in answers[:60])
        assert answers[60]["error"] == "rate_limited"                          # the 61st quote within a minute
        assert call(client, "get_shipping_options", QUOTE, "198.51.100.9").get("error") != "rate_limited"
        # api/main.py hands its own limiter to build_mcp, so the REST route and the MCP tool use one bucket per client
        for _ in range(60):
            limits.allow("shipping:192.0.2.5", 60, 60)
        assert call(client, "get_shipping_options", QUOTE, "192.0.2.5")["error"] == "rate_limited"


def test_mcp_checkout_is_limited_to_twenty_an_hour_per_client():
    args = {"product_id": "p", "variant_id": "v", "quantity": 1, "buyer_name": "A", "buyer_phone": "62811",
            "address_line": "Jl 1", "city": "Bandung", "postal_code": "40115"}
    with TestClient(mcp_app(RateLimiter())) as client:
        answers = [call(client, "create_checkout", args, "203.0.113.50") for _ in range(21)]
        assert all(a.get("error") != "rate_limited" for a in answers[:20])
        assert answers[20]["error"] == "rate_limited"


def test_the_real_app_passes_its_limiter_to_the_mcp_server():
    import inspect
    assert "build_mcp(ctx, limits)" in inspect.getsource(api)


def test_the_limit_context_is_not_part_of_the_tool_schema():
    for tool in asyncio.run(api.mcp.list_tools()):
        assert "mcp_ctx" not in json.dumps(tool.inputSchema), tool.name


def test_in_process_tool_calls_are_not_limited():
    # No HTTP request behind the call (tests, stdio): nothing to key a limit on.
    for _ in range(70):
        result = asyncio.run(api.mcp.call_tool("get_shipping_options", QUOTE))
    assert "rate_limited" not in json.dumps(result, default=str)


# ---------------------------------------------------------------- seller side
class CountingAgent:
    def __init__(self):
        self.calls = 0

    async def respond(self, seller_id, text):
        self.calls += 1
        return "ok"


def gemini_ctx(budget, per_day=0):
    ctx = build_context(Settings(etalase_env="dev", _env_file=None, google_api_key="k", seller_ai_calls_per_hour=budget,
                                 seller_ai_calls_per_day=per_day))
    ctx.seller_agent = CountingAgent()
    return ctx


def test_a_seller_cannot_run_up_unlimited_gemini_calls():
    ctx = gemini_ctx(3)
    seller = make_seller(ctx)
    ctx.messenger.outbox.clear()
    for i in range(5):
        asyncio.run(ctx.inbox.handle_text(seller.id, f"hello {i}"))
    assert ctx.seller_agent.calls == 3
    texts = [m.text for m in ctx.messenger.outbox]
    assert texts.count("ok") == 3 and sum("faster than I can handle" in t for t in texts) == 2


def test_catalog_builds_share_the_same_budget():
    ctx = gemini_ctx(2)
    seller = make_seller(ctx)
    ctx.inbox.create_catalog(seller.id, ["Voal hijab black 5 price 85k"])
    ctx.inbox.create_catalog(seller.id, ["Pashmina navy 3 price 60k"])
    ctx.messenger.outbox.clear()
    assert ctx.inbox.create_catalog(seller.id, ["Another product price 10k"]) == []
    assert "faster than I can handle" in ctx.messenger.outbox[-1].text


def test_budgets_are_per_seller():
    ctx = gemini_ctx(1)
    a, b = make_seller(ctx, "A", "6281111111111"), make_seller(ctx, "B", "6282222222222")
    asyncio.run(ctx.inbox.handle_text(a.id, "hi"))
    asyncio.run(ctx.inbox.handle_text(a.id, "again"))
    asyncio.run(ctx.inbox.handle_text(b.id, "hi"))
    assert ctx.seller_agent.calls == 2                      # A's second message was refused, B's first went through


def test_offline_mode_is_never_limited(ctx):
    seller = make_seller(ctx)
    assert not ctx.settings.gemini_enabled
    for i in range(100):
        assert ctx.inbox._ai_allowed(seller.id)


def test_a_daily_ceiling_stops_calls_even_when_every_hour_is_within_its_budget():
    ctx = gemini_ctx(100, per_day=4)
    seller = make_seller(ctx)
    ctx.messenger.outbox.clear()
    for i in range(7):
        asyncio.run(ctx.inbox.handle_text(seller.id, f"hello {i}"))
    assert ctx.seller_agent.calls == 4


def test_a_call_refused_by_the_hourly_budget_does_not_use_up_the_daily_one():
    ctx = gemini_ctx(2, per_day=5)
    seller = make_seller(ctx)
    for i in range(6):
        asyncio.run(ctx.inbox.handle_text(seller.id, f"hello {i}"))
    assert ctx.seller_agent.calls == 2
    assert ctx.inbox._limits.count(f"ai-day:{seller.id}", 86400) == 2


def test_no_daily_ceiling_by_default():
    assert Settings(_env_file=None).seller_ai_calls_per_day == 0
