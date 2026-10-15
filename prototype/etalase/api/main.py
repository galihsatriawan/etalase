"""HTTP entry point (Cloud Run). Run locally: uvicorn etalase.api.main:app --reload --port 8080"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..adapters import instagram
from ..adapters.messaging import ConsoleMessenger, Inbound, parse_whatsapp_webhook
from ..connector.service import OrderStatusIn, ProductIn, StockIn
from ..container import AppContext, build_context
from ..gateway import discovery, ucp as ucp_protocol
from ..gateway.mcp_server import build_mcp
from ..gateway.tools import order_view
from ..models import Address, CheckoutError, Connection, Seller
from ..security import (DASHBOARD_CSP, SECURITY_HEADERS, RateLimiter, client_ip, require_dev,
                        require_webhook_token)

log = logging.getLogger("etalase.api")

ctx: AppContext = build_context()   # refuses to start on an insecure configuration (see security.py)
mcp = build_mcp(ctx)
limits = RateLimiter()
MAX_BODY_BYTES = 2_000_000
SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


def rate_limit(request: Request, bucket: str, limit: int, window_s: int) -> None:
    if not limits.allow(f"{bucket}:{client_ip(request)}", limit, window_s):
        raise HTTPException(429, "too many requests, slow down")


def webhook_token(request: Request) -> None:
    require_webhook_token(request, ctx.settings)


def dev_only(request: Request) -> None:
    require_dev(request, ctx.settings)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async def expiry_loop():  # production: Cloud Tasks per order, or Cloud Scheduler -> /tasks/expire
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(ctx.checkout.expire_due)
            except Exception:  # one bad order must not stop expiry for everyone
                log.exception("expiry run failed")

    task = asyncio.create_task(expiry_loop())
    async with mcp.session_manager.run():
        yield
    task.cancel()


app = FastAPI(title="Etalase API", version="0.1.0", lifespan=lifespan,
              description="Make WhatsApp & Instagram sellers discoverable and buyable by AI shopping agents.")
app.mount("/mcp", mcp.streamable_http_app())


@app.middleware("http")
async def guard(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "request too large"}, status_code=413)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = DASHBOARD_CSP
    return response


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


@app.get("/stores/{seller_id}/products.jsonld")
def products_jsonld(seller_id: str):
    return discovery.schema_org_products(ctx, seller_id)


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
    for msg in parse_whatsapp_webhook(await request.json()):
        try:
            await ctx.inbox.handle(msg)
        except Exception:  # acknowledge anyway, or Meta retries the same message for days
            log.exception("failed to handle WhatsApp message %s", msg.message_id)
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
    return {"expired": ctx.checkout.expire_due()}


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
    return {"id": s.id, "name": s.name, "whatsapp": s.whatsapp, "stock_owner": s.stock_owner.value, "kyc": s.kyc_status}


@app.get("/api/me/products", tags=["seller"])
def my_products(seller_id: str = Depends(current_seller)):
    return [p.model_dump(mode="json") for p in ctx.store.list_products(seller_id=seller_id)]


@app.get("/api/me/orders", tags=["seller"])
def my_orders(seller_id: str = Depends(current_seller)):
    orders = sorted(ctx.store.list_orders(seller_id), key=lambda o: o.created_at, reverse=True)
    return [order_view(o) | {"payout": o.payout.model_dump() if o.payout else None, "buyer_city": o.buyer.city}
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
    return (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


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
    return seller.model_dump()


@dev.post("/whatsapp", dependencies=[Depends(dev_only)])
async def dev_whatsapp(body: DevInbound):
    """Simulate a seller's WhatsApp message; returns the replies Etalase sent back."""
    outbox = ctx.messenger.outbox if isinstance(ctx.messenger, ConsoleMessenger) else []
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
    outbox = ctx.messenger.outbox if isinstance(ctx.messenger, ConsoleMessenger) else []
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


@dev.post("/instagram/import", dependencies=[Depends(dev_only)])
def dev_instagram_import(seller_id: str):
    """Import the sample Instagram posts (samples/instagram_posts.json) for a seller."""
    sample = Path(__file__).resolve().parents[2] / "samples" / "instagram_posts.json"
    outbox = ctx.messenger.outbox if isinstance(ctx.messenger, ConsoleMessenger) else []
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
