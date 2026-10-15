"""HTTP entry point (Cloud Run). Run locally: uvicorn etalase.api.main:app --reload --port 8080"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..adapters import instagram
from ..adapters.messaging import Inbound, parse_whatsapp_webhook, whatsapp_failed_statuses
from ..adapters.telegram import SECRET_HEADER as TELEGRAM_SECRET_HEADER
from ..adapters.telegram import parse_telegram_update
from ..connector.service import OrderStatusIn, ProductIn, StockIn
from ..container import AppContext, build_context
from ..gateway import discovery, pages, storefront, ucp as ucp_protocol
from ..gateway.hosts import ShopHostMiddleware
from ..gateway.mcp_server import build_mcp
from ..gateway.tools import order_view
from ..models import Address, CheckoutError, Connection, Seller, now
from ..seller import channels, slugs
from ..security import (DASHBOARD_CSP, BodyLimitMiddleware, SECURITY_HEADERS, RateLimiter, client_ip, require_dev,
                        require_operator_token, require_webhook_token)

log = logging.getLogger("etalase.api")

ctx: AppContext = build_context()   # refuses to start on an insecure configuration (see security.py)
limits = RateLimiter()
mcp = build_mcp(ctx, limits)
MAX_BODY_BYTES = 2_000_000
SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
STATIC_DIR = Path(__file__).resolve().parents[1] / "gateway" / "static"


def rate_limit(request: Request, bucket: str, limit: int, window_s: int) -> None:
    if not limits.allow(f"{bucket}:{client_ip(request)}", limit, window_s):
        raise HTTPException(429, "too many requests, slow down")


def webhook_token(request: Request) -> None:
    require_webhook_token(request, ctx.settings)


def operator_token(request: Request) -> None:
    require_operator_token(request, ctx.settings)


def dev_only(request: Request) -> None:
    require_dev(request, ctx.settings)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async def expiry_loop():  # production: Cloud Tasks per order, or Cloud Scheduler -> /tasks/expire
        while True:
            await asyncio.sleep(60)
            try:  # expiry, return timers and due payouts
                await asyncio.to_thread(ctx.process_due)
            except Exception:  # one bad order must not stop the job for everyone
                log.exception("background run failed")

    task = asyncio.create_task(expiry_loop())
    async with mcp.session_manager.run():
        yield
    task.cancel()


app = FastAPI(title="Etalase API", version="0.1.0", lifespan=lifespan,
              description="Make WhatsApp & Instagram sellers discoverable and buyable by AI shopping agents.")
app.mount("/mcp", mcp.streamable_http_app())


@app.middleware("http")
async def guard(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = DASHBOARD_CSP
    return response


app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)   # counts bytes read, so chunked uploads are capped too
app.add_middleware(ShopHostMiddleware, ctx=ctx)   # added last = outermost: it maps the host before the guard looks at the path


@app.get("/healthz")
def healthz():
    return {"ok": True, "modes": ctx.modes}


# ------------------------------------------------------------------ discovery
@app.get("/.well-known/ucp")
def ucp():
    return ucp_protocol.ucp_profile(ctx)


app.include_router(ucp_protocol.build_router(ctx, limits))   # /ucp/v1/checkout-sessions ... (UCP Checkout, REST)


@app.get("/checkout/{checkout_id}", response_class=HTMLResponse, include_in_schema=False)
def hosted_checkout(request: Request, checkout_id: str):
    rate_limit(request, "pay-page", 600, 60)
    return ucp_protocol.pay_page_response(ctx, checkout_id)


# ------------------------------------------------------------------ the public store link (people and agents, same page)
def same_origin(request: Request) -> None:
    """A form post must come from our own pages. Browsers always send Origin on cross-site posts."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "cross-site form posts are not accepted")


@app.get("/stores/{seller_id}", response_class=HTMLResponse, include_in_schema=False)
def store_front(request: Request, seller_id: str):
    rate_limit(request, "storefront", 600, 60)
    return storefront.store_page(ctx, seller_id)


@app.get("/stores/{seller_id}/p/{product_id}", response_class=HTMLResponse, include_in_schema=False)
def store_product(request: Request, seller_id: str, product_id: str, v: str = ""):
    rate_limit(request, "storefront", 600, 60)
    return storefront.product_page(ctx, seller_id, product_id, v)


@app.get("/stores/{seller_id}/buy/{variant_id}", response_class=HTMLResponse, include_in_schema=False)
def store_buy_form(request: Request, seller_id: str, variant_id: str):
    rate_limit(request, "storefront", 600, 60)
    return storefront.buy_form(ctx, seller_id, variant_id)


@app.post("/stores/{seller_id}/buy/{variant_id}", response_class=HTMLResponse, include_in_schema=False)
async def store_buy(request: Request, seller_id: str, variant_id: str):
    same_origin(request)
    rate_limit(request, "checkout", 20, 3600)     # the same bucket as the REST and MCP checkouts
    return await asyncio.to_thread(storefront.buy_submit, ctx, seller_id, variant_id, await request.body())


@app.get("/pay/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def order_status_page(request: Request, order_id: str):
    rate_limit(request, "pay-page", 600, 60)
    return storefront.order_page(ctx, order_id)


@app.get("/pay/{order_id}/return", response_class=HTMLResponse, include_in_schema=False)
def order_return_form(request: Request, order_id: str):
    rate_limit(request, "pay-page", 600, 60)
    return storefront.return_form(ctx, order_id)


@app.post("/pay/{order_id}/return", response_class=HTMLResponse, include_in_schema=False)
async def order_return_submit(request: Request, order_id: str):
    same_origin(request)
    rate_limit(request, "return", 10, 3600)       # the same bucket as the agent return tools
    return await asyncio.to_thread(storefront.return_submit, ctx, order_id, await request.body())


@app.get("/stores/{seller_id}/products.jsonld")
def products_jsonld(seller_id: str):
    return discovery.schema_org_products(ctx, seller_id)


@app.get("/stores/{seller_id}/returns", response_class=HTMLResponse, include_in_schema=False)
def return_policy_page(request: Request, seller_id: str):
    rate_limit(request, "policy", 600, 60)
    return ucp_protocol.policy_page_response(ctx, seller_id)


@app.get("/stores/{seller_id}/returns.json")
def return_policy_json(request: Request, seller_id: str):
    rate_limit(request, "policy", 600, 60)
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        raise HTTPException(404, "store not found")
    return {"return_policy": ctx.returns.policy_view(seller), "track_record": ctx.checkout.track_record(seller.id)}


# ------------------------------------------------------------------ agent REST (mirror of the MCP tools)
agent = APIRouter(prefix="/agent/v1", tags=["agent"])


class CheckoutIn(BaseModel):
    product_id: str = Field(max_length=40)
    variant_id: str = Field(max_length=40)
    quantity: int = Field(ge=1, le=100)
    buyer_name: str = Field(min_length=1, max_length=80)
    buyer_phone: str = Field(min_length=8, max_length=20)
    address_line: str = Field(min_length=5, max_length=200)
    city: str = Field(max_length=60)
    postal_code: str = Field(max_length=10)
    country: str = Field(default="ID", min_length=2, max_length=2)
    courier: str | None = Field(default=None, max_length=20)


@agent.get("/search")
def agent_search(request: Request, q: str, max_price_idr: int | None = None, country: str = "ID", limit: int = 5):
    rate_limit(request, "search", 120, 60)
    return ctx.gateway.search_products(q[:200], max_price_idr, country, limit)


@agent.get("/products/{product_id}")
def agent_product(product_id: str):
    return ctx.gateway.get_product(product_id)


@agent.get("/products/{product_id}/shipping-options")
def agent_shipping(request: Request, product_id: str, variant_id: str, quantity: int, postal_code: str):
    rate_limit(request, "shipping", 60, 60)  # each call can cost a courier-API request
    return ctx.gateway.get_shipping_options(product_id, variant_id[:40], max(1, min(quantity, 100)), postal_code[:10])


@agent.post("/checkouts")
def agent_checkout(request: Request, body: CheckoutIn):
    rate_limit(request, "checkout", 20, 3600)
    return ctx.gateway.create_checkout(**body.model_dump())


@agent.get("/checkouts/{checkout_id}")
def agent_checkout_status(checkout_id: str):
    return ctx.gateway.get_checkout_status(checkout_id)


class ReturnIn(BaseModel):
    buyer_phone: str = Field(min_length=8, max_length=20)
    reason: str = Field(max_length=30)
    note: str = Field(default="", max_length=500)
    evidence_urls: list[str] = Field(default_factory=list, max_length=5)


class ReturnTrackingIn(BaseModel):
    buyer_phone: str = Field(min_length=8, max_length=20)
    waybill: str = Field(min_length=6, max_length=40)


@agent.post("/checkouts/{checkout_id}/return")
def agent_request_return(request: Request, checkout_id: str, body: ReturnIn):
    rate_limit(request, "return", 10, 3600)
    return ctx.gateway.request_return(checkout_id[:40], body.buyer_phone, body.reason, body.note, body.evidence_urls)


@agent.post("/checkouts/{checkout_id}/return/tracking")
def agent_return_tracking(request: Request, checkout_id: str, body: ReturnTrackingIn):
    rate_limit(request, "return", 10, 3600)
    return ctx.gateway.submit_return_tracking(checkout_id[:40], body.buyer_phone, body.waybill)


app.include_router(agent)


# ------------------------------------------------------------------ webhooks
@app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(request: Request):
    q = request.query_params
    token = q.get("hub.verify_token") or ""
    if q.get("hub.mode") == "subscribe" and hmac.compare_digest(token.encode(), ctx.settings.whatsapp_verify_token.encode()):
        return q.get("hub.challenge", "")
    raise HTTPException(403, "verification failed")


@app.post("/webhooks/whatsapp")
async def whatsapp_inbound(request: Request):
    raw = await request.body()
    app_secret = ctx.settings.whatsapp_app_secret
    if ctx.modes["messaging"] == "whatsapp" or app_secret:
        # Meta signs every delivery with the app secret; without a valid signature, drop it.
        if not app_secret:
            raise HTTPException(503, "WHATSAPP_APP_SECRET not configured")
        expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), request.headers.get("x-hub-signature-256", "").encode()):
            raise HTTPException(401, "bad signature")
    else:
        require_webhook_token(request, ctx.settings)  # console mode: our own token
    try:
        body = await request.json()
        for failed in whatsapp_failed_statuses(body):   # delivery failures show up here, after the API accepted the send
            log.warning("WhatsApp delivery failed: %s", failed)
        messages = parse_whatsapp_webhook(body)
    except Exception:   # a body we cannot read is acknowledged and dropped; a 500 would make Meta retry it for days
        log.warning("ignoring a malformed WhatsApp webhook body", exc_info=True)
        return {"ok": True}
    for msg in messages:
        try:
            await ctx.inbox.handle(msg)
        except Exception:  # acknowledge anyway, or Meta retries the same message for days
            log.exception("failed to handle WhatsApp message %s", msg.message_id)
    return {"ok": True}


@app.post("/webhooks/telegram")
async def telegram_inbound(request: Request):
    """Telegram Bot API updates. With a real bot the call must carry the secret set with setWebhook; without one (local
    demo, tests) it is protected like the console WhatsApp webhook."""
    if ctx.telegram is not None:
        secret = ctx.settings.telegram_webhook_secret
        if not secret:
            raise HTTPException(503, "TELEGRAM_WEBHOOK_SECRET not configured")
        if not hmac.compare_digest(request.headers.get(TELEGRAM_SECRET_HEADER, "").encode(), secret.encode()):
            raise HTTPException(401, "bad secret")
    else:
        require_webhook_token(request, ctx.settings)
    try:
        update = await request.json()
        messages = parse_telegram_update(update) if isinstance(update, dict) else []
    except Exception:   # unreadable update: acknowledge it, or Telegram redelivers the same bytes
        log.warning("ignoring a malformed Telegram update", exc_info=True)
        return {"ok": True}
    for msg in messages:
        if msg.callback_id and ctx.telegram is not None:
            ctx.telegram.answer_callback(msg.callback_id)
        try:
            await ctx.inbox.handle(msg)
        except Exception:  # acknowledge anyway, or Telegram keeps redelivering the same update
            log.exception("failed to handle Telegram message %s", msg.message_id)
    return {"ok": True}


@app.post("/webhooks/payment")
async def payment_webhook(request: Request):
    if ctx.payment.name == "mock":  # real providers authenticate with their own callback token
        require_webhook_token(request, ctx.settings)
    try:
        parsed = ctx.payment.parse_webhook({k.lower(): v for k, v in request.headers.items()}, await request.json())
    except PermissionError:
        raise HTTPException(401, "bad callback token")
    if parsed and parsed[1] == "paid":
        ctx.checkout.payment_succeeded(parsed[0])
    return {"ok": True}


@app.post("/webhooks/shipping", dependencies=[Depends(webhook_token)])
async def shipping_webhook(request: Request):
    """Register the URL with ?token=WEBHOOK_TOKEN. Delivery is re-checked with the courier
    before any payout (CheckoutService.shipment_update)."""
    parsed = ctx.shipping.parse_webhook(await request.json())
    if parsed:
        ctx.checkout.shipment_update(*parsed)
    return {"ok": True}


@app.post("/tasks/expire", dependencies=[Depends(webhook_token)])
def expire_task():
    """The background job (Cloud Scheduler calls it every minute): expiry, return timers, due payouts."""
    return ctx.process_due()


class DisputeResolution(BaseModel):
    order_id: str = Field(max_length=40)
    outcome: str = Field(pattern="^(refund|release)$")


@app.get("/tasks/disputes", dependencies=[Depends(operator_token)])
def open_disputes():
    """Returns the seller disputed. Production: the operator console reads the same data. Header X-Etalase-Operator-Token."""
    return ctx.returns.disputes()


@app.post("/tasks/resolve-dispute", dependencies=[Depends(operator_token)])
def resolve_dispute(body: DisputeResolution):
    try:
        return order_view(ctx.returns.resolve_dispute(body.order_id, body.outcome))
    except CheckoutError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ Connector API (OMS, plugins, website builders)
connector = APIRouter(prefix="/connector/v1", tags=["connector"])


def connection(x_etalase_key: str | None = Header(default=None)):
    conn = ctx.connector.authenticate(x_etalase_key)
    if conn is None:
        raise HTTPException(401, "invalid X-Etalase-Key")
    return conn


@connector.put("/products/{external_id}")
def connector_upsert(external_id: str, body: ProductIn, conn=Depends(connection)):
    p = ctx.connector.upsert_product(conn, external_id, body)
    return {"product_id": p.id, "status": p.status.value, "readiness": p.readiness_score, "missing": p.readiness_missing}


@connector.patch("/stock")
def connector_stock(items: list[StockIn], conn=Depends(connection)):
    return ctx.connector.update_stock(conn, items)


@connector.post("/orders/{order_id}/status")
def connector_order_status(order_id: str, body: OrderStatusIn, conn=Depends(connection)):
    try:
        return order_view(ctx.connector.order_status(conn, order_id, body))
    except (CheckoutError, ValueError) as e:
        raise HTTPException(400, str(e))


app.include_router(connector)


# ------------------------------------------------------------------ seller dashboard API (WhatsApp OTP sign-in)
class OtpRequest(BaseModel):
    whatsapp: str


class OtpVerify(BaseModel):
    whatsapp: str
    code: str


@app.post("/auth/otp", tags=["seller"])
def auth_otp(request: Request, body: OtpRequest):
    rate_limit(request, "otp", 10, 3600)  # plus per-number cooldown and hourly cap in OtpAuth
    ctx.auth.request(body.whatsapp.strip().lstrip("+")[:20])
    return {"sent": True}


@app.post("/auth/verify", tags=["seller"])
def auth_verify(request: Request, body: OtpVerify):
    rate_limit(request, "verify", 30, 3600)
    token = ctx.auth.verify(body.whatsapp.strip().lstrip("+")[:20], body.code.strip()[:10])
    if not token:
        raise HTTPException(401, "invalid or expired code")
    return {"token": token}


def current_seller(authorization: str | None = Header(default=None)) -> str:
    seller_id = ctx.auth.seller_from_token((authorization or "").removeprefix("Bearer ").strip())
    if not seller_id:
        raise HTTPException(401, "sign in required")
    return seller_id


@app.get("/api/me", tags=["seller"])
def me(seller_id: str = Depends(current_seller)):
    s = ctx.store.get_seller(seller_id)
    return {"id": s.id, "name": s.name, "whatsapp": s.whatsapp, "stock_owner": s.stock_owner.value, "kyc": s.kyc_status,
            "store_url": slugs.store_url(ctx.settings, s),
            "slug": s.slug, "subdomains": ({"domain": ctx.settings.store_base_domain} if slugs.subdomains_enabled(ctx.settings) else None),
            "return_policy": ctx.returns.policy_view(s), "track_record": ctx.checkout.track_record(s.id),
            "channel": s.channel,
            "telegram": {"available": channels.telegram_available(ctx), "linked": bool(s.telegram_chat_id)}}


class SlugIn(BaseModel):
    slug: str = Field(max_length=40)


@app.put("/api/me/slug", tags=["seller"])
def set_slug(request: Request, body: SlugIn, seller_id: str = Depends(current_seller)):
    """The shop's own address, <slug>.<STORE_BASE_DOMAIN>. The previous address stops working."""
    rate_limit(request, "slug", 20, 3600)
    if not slugs.subdomains_enabled(ctx.settings):
        raise HTTPException(404, "shop addresses are not enabled on this server")
    try:
        slugs.claim(ctx, seller_id, body.slug)
    except slugs.SlugError as e:
        raise HTTPException(409 if "taken" in str(e) else 400, str(e))
    return me(seller_id)


class ChannelIn(BaseModel):
    channel: str = Field(max_length=20)


@app.post("/api/me/telegram/link", tags=["seller"])
def telegram_link(request: Request, seller_id: str = Depends(current_seller)):
    """A 10-minute code and deep link that ties a Telegram chat to this shop (see seller/channels.py)."""
    rate_limit(request, "tg-link", 10, 3600)
    try:
        return channels.start_link(ctx, seller_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.put("/api/me/channel", tags=["seller"])
def set_channel(body: ChannelIn, seller_id: str = Depends(current_seller)):
    """Where Etalase sends orders, stock checks and payouts: whatsapp (default) or a linked telegram chat."""
    try:
        channels.set_channel(ctx, seller_id, body.channel)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return me(seller_id)


class ReturnPolicyIn(BaseModel):
    accepts_change_of_mind: bool | None = None
    note: str | None = Field(default=None, max_length=300)


@app.put("/api/me/return-policy", tags=["seller"])
def set_return_policy(body: ReturnPolicyIn, seller_id: str = Depends(current_seller)):
    ctx.returns.set_policy(seller_id, body.accepts_change_of_mind, body.note)
    return ctx.returns.policy_view(ctx.store.get_seller(seller_id))


@app.get("/api/me/products", tags=["seller"])
def my_products(seller_id: str = Depends(current_seller)):
    return [p.model_dump(mode="json") for p in ctx.store.list_products(seller_id=seller_id)]


@app.get("/api/me/orders", tags=["seller"])
def my_orders(seller_id: str = Depends(current_seller)):
    orders = sorted(ctx.store.list_orders(seller_id), key=lambda o: o.created_at, reverse=True)
    # The buyer's note and photo links are shown to the seller here only (never pushed into chat); the dashboard escapes them.
    return [order_view(o) | {"payout": o.payout.model_dump() if o.payout else None, "buyer_city": o.buyer.city,
                             "buyer_first_name": (o.buyer.name.split() or [""])[0][:30], "created_at": o.created_at.isoformat(timespec="seconds"),
                             "payout_due_at": o.payout_due_at.isoformat(timespec="minutes") if o.payout_due_at else None,
                             "return_detail": ({"note": o.return_request.note, "evidence": o.return_request.evidence}
                                               if o.return_request else None)}
            for o in orders]


# ------------------------------------------------------------------ Instagram catalog import
@app.get("/connect/instagram", tags=["seller"])
def instagram_connect(seller_id: str = Depends(current_seller)):
    """Start Instagram Login for the signed-in seller (professional accounts)."""
    s = ctx.settings
    if not (s.instagram_app_id and s.instagram_app_secret):
        raise HTTPException(501, "Instagram app is not configured (INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET)")
    # Short-lived signed state (not a session token), so the callback knows which seller connected.
    state = ctx.auth.sign(f"ig.{seller_id}.{int(time.time()) + 600}")
    return {"authorize_url": instagram.authorize_url(s.instagram_app_id, f"{s.public_base_url}/connect/instagram/callback", state)}


@app.get("/connect/instagram/callback", tags=["seller"], response_class=HTMLResponse)
def instagram_callback(code: str, state: str):
    payload = ctx.auth.unsign(state) or ""
    parts = payload.split(".")
    if len(parts) != 3 or parts[0] != "ig" or not parts[2].isdigit() or int(parts[2]) < time.time():
        raise HTTPException(401, "invalid or expired state")
    seller_id = parts[1]
    s = ctx.settings
    token = instagram.exchange_code(s.instagram_app_id, s.instagram_app_secret,
                                    f"{s.public_base_url}/connect/instagram/callback", code)
    conn = Connection(seller_id=seller_id, kind="instagram", access_token=token)
    ctx.store.put_connection(conn)
    ctx.inbox.import_instagram(seller_id, instagram.InstagramGraphSource(token))
    return "<p>Instagram connected. Check WhatsApp for your imported products.</p>"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    """The seller dashboard: one static page that shares the buyer pages' design tokens, buttons, fields and icons."""
    html = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
    return html.replace("/*@@BASE_CSS@@*/", pages.BASE_CSS).replace("<!--@@SPRITE@@-->", pages.sprite())


@app.get("/static/{name}", include_in_schema=False)
def static_asset(name: str):
    """The self-hosted font, by exact file name from a fixed list (never a path), cached for a year."""
    kind = pages.FONT_FILES.get(name)
    if kind is None:
        raise HTTPException(404)
    return FileResponse(STATIC_DIR / name, media_type=kind, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/media/{name}", include_in_schema=False)
def media(name: str):
    """Product images by file name only (no paths), from the media folder or bundled samples."""
    if "/" in name or "\\" in name or name.startswith(".") or not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(404)
    for root in (Path(ctx.media_dir).resolve(), SAMPLES_DIR):
        path = (root / name).resolve()
        if path.parent == root and path.is_file():
            return FileResponse(path)
    raise HTTPException(404)


# ------------------------------------------------------------------ dev-only simulation
# ETALASE_ENV=dev only. On a public URL every call needs the X-Dev-Token header (DEV_TOKEN).
dev = APIRouter(prefix="/dev", tags=["dev"])


class DevSeller(BaseModel):
    name: str
    whatsapp: str
    city: str = "Jakarta"
    postal_code: str = "12950"
    address: str = "Jl. Contoh No. 1"


class DevInbound(BaseModel):
    sender: str
    text: str = ""
    button_id: str = ""
    image_path: str = ""


@dev.post("/sellers", dependencies=[Depends(dev_only)])
def dev_create_seller(body: DevSeller):
    account = "mock-sub-account" if ctx.payment.name == "mock" else None  # real test mode: platform account
    seller = Seller(name=body.name, whatsapp=body.whatsapp, kyc_status="verified", payout_account_id=account,
                    origin=Address(name=body.name, phone=body.whatsapp, line1=body.address, city=body.city,
                                   postal_code=body.postal_code))
    ctx.store.put_seller(seller)
    if slugs.subdomains_enabled(ctx.settings):
        seller = slugs.assign(ctx, seller.id)
    return seller.model_dump()


@dev.post("/whatsapp", dependencies=[Depends(dev_only)])
async def dev_whatsapp(body: DevInbound):
    """Simulate a seller's WhatsApp message; returns the replies Etalase sent back."""
    outbox = getattr(ctx.messenger, "outbox", [])
    start = len(outbox)
    if body.button_id:
        msg = Inbound(body.sender, "button", button_id=body.button_id)
    elif body.image_path:
        msg = Inbound(body.sender, "image", media_id=body.image_path, text=body.text)
    else:
        msg = Inbound(body.sender, "text", text=body.text)
    await ctx.inbox.handle(msg)
    return [{"to": m.to, "text": m.text, "buttons": m.buttons} for m in outbox[start:]]


@dev.get("/outbox", dependencies=[Depends(dev_only)])
def dev_outbox(to: str | None = None):
    outbox = getattr(ctx.messenger, "outbox", [])
    return [{"to": m.to, "text": m.text, "buttons": m.buttons} for m in outbox if to is None or m.to == to]


@dev.post("/pay/{order_id}", dependencies=[Depends(dev_only)])
def dev_pay(order_id: str):
    order = ctx.store.get_order(order_id)
    if not order or not order.payment:
        raise HTTPException(404, "order has no pending payment")
    return order_view(ctx.checkout.payment_succeeded(order.payment.reference))


@dev.post("/deliver/{order_id}", dependencies=[Depends(dev_only)])
def dev_deliver(order_id: str):
    return order_view(ctx.checkout.shipment_update(order_id, "delivered"))


@dev.post("/release/{order_id}", dependencies=[Depends(dev_only)])
def dev_release(order_id: str):
    """Skip the return window: the order's payout is due now. Runs the background job once, like the real clock would."""
    def due(o):
        o.payout_due_at = now()
        return True
    if ctx.store.update_order(order_id, due) is None:
        raise HTTPException(404, "order not found")
    ctx.process_due()
    return order_view(ctx.store.get_order(order_id))


@dev.post("/instagram/import", dependencies=[Depends(dev_only)])
def dev_instagram_import(seller_id: str):
    """Import the sample Instagram posts (samples/instagram_posts.json) for a seller."""
    sample = Path(__file__).resolve().parents[2] / "samples" / "instagram_posts.json"
    outbox = getattr(ctx.messenger, "outbox", [])
    start = len(outbox)
    drafts = ctx.inbox.import_instagram(seller_id, instagram.SampleInstagramSource(str(sample)))
    return {"drafts": [p.title for p in drafts],
            "replies": [{"to": m.to, "text": m.text, "buttons": m.buttons} for m in outbox[start:]]}


@dev.post("/connections", dependencies=[Depends(dev_only)])
def dev_connection(seller_id: str, kind: str = "connector_api", order_webhook_url: str | None = None):
    try:
        conn, key = ctx.connector.create_connection(seller_id, kind, order_webhook_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"connection_id": conn.id, "api_key": key, "webhook_secret": conn.webhook_secret,
            "note": "The API key is shown once."}


app.include_router(dev)
