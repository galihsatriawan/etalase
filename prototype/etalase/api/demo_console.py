"""Routes of the judge sandbox. Mounted by api/main.py only when DEMO_MODE is on (and security.validate_settings has checked that
payment, shipping and messaging are all simulated). Every route answers for the one shared demo seller and nothing else."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import demo
from ..adapters.messaging import Inbound
from ..container import AppContext
from ..gateway import pages
from ..security import DASHBOARD_CSP, RateLimiter, client_ip

PAGE = Path(__file__).with_name("demo.html")

# The sandbox shares one AI budget (SELLER_AI_CALLS_PER_HOUR and _PER_DAY) between every visitor. This keeps one visitor from
# using all of it: typed messages and sample photos count, button taps do not (they never reach the model).
AI_SENDS_PER_VISITOR_PER_HOUR = 25


class SendIn(BaseModel):
    text: str = Field(default="", max_length=1000)
    button_id: str = Field(default="", max_length=80)
    photo: str = Field(default="", max_length=60)


def build_router(ctx: AppContext, limits: RateLimiter) -> APIRouter:
    router = APIRouter(prefix="/demo", include_in_schema=False)

    def limited(request: Request, bucket: str, limit: int, window_s: int, detail: str = "too many requests, slow down") -> None:
        if not limits.allow(f"{bucket}:{client_ip(request)}", limit, window_s):
            raise HTTPException(429, detail)

    def same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(403, "cross-site posts are not accepted")

    @router.get("", response_class=HTMLResponse)
    def console_page():
        html = PAGE.read_text(encoding="utf-8").replace("/*@@BASE_CSS@@*/", pages.BASE_CSS).replace("<!--@@SPRITE@@-->", pages.sprite())
        return HTMLResponse(html, headers={"Content-Security-Policy": DASHBOARD_CSP, "Cache-Control": "no-store"})

    @router.get("/api/state")
    def state(request: Request):
        limited(request, "demo-state", 6000, 3600)
        return JSONResponse(demo.console_state(ctx), headers={"Cache-Control": "no-store"})

    @router.post("/api/send")
    async def send(request: Request, body: SendIn):
        """The demo seller 'writes' in WhatsApp: a text, a button tap, or one of the sample photos with an optional caption."""
        same_origin(request)
        limited(request, "demo-send", 150, 3600)
        if not body.button_id:
            limited(request, "demo-ai", AI_SENDS_PER_VISITOR_PER_HOUR, 3600,
                    "The sandbox limits how many messages one visitor can send per hour, to keep its AI cost small. Please try again later.")
        text = body.text.strip()
        if body.button_id:
            msg, shown = Inbound(demo.DEMO_NUMBER, "button", button_id=body.button_id), dict(text=text or body.button_id, tapped=True)
        elif body.photo:
            if body.photo not in demo.SAMPLE_PHOTOS:
                raise HTTPException(400, "unknown photo")
            msg = Inbound(demo.DEMO_NUMBER, "image", media_id=str(demo.SAMPLES_DIR / body.photo), mime_type="image/png", text=text)
            shown = dict(text=text, photo=body.photo)
        elif text:
            msg, shown = Inbound(demo.DEMO_NUMBER, "text", text=text), dict(text=text)
        else:
            raise HTTPException(400, "nothing to send")
        demo.record_sent(ctx, **shown)
        await ctx.inbox.handle(msg)
        return demo.console_state(ctx)

    @router.post("/api/orders/{order_id}/{action}")
    def simulate(request: Request, order_id: str, action: str):
        same_origin(request)
        limited(request, "demo-sim", 150, 3600)
        try:
            demo.simulate(ctx, order_id, action)
        except KeyError:
            raise HTTPException(404, "order not found")
        except demo.DemoError as e:
            raise HTTPException(409, str(e))
        return demo.console_state(ctx)

    @router.post("/pay/{order_id}")
    def pay_from_order_page(request: Request, order_id: str):
        """The button on the order page. Whatever happens, the buyer lands back on the order page, which shows the real state."""
        same_origin(request)
        limited(request, "demo-pay", 60, 3600)
        try:
            demo.simulate(ctx, order_id, "pay")
        except (KeyError, demo.DemoError):
            pass
        return RedirectResponse(f"/pay/{order_id[:64]}", status_code=303, headers={"Cache-Control": "no-store"})

    @router.post("/api/signin")
    def signin(request: Request):
        """Sign in to the dashboard as the demo seller without the WhatsApp code (the code also works: it goes to /demo)."""
        same_origin(request)
        limited(request, "demo-signin", 60, 3600)
        return {"token": ctx.auth.issue(demo.DEMO_SELLER_ID)}

    return router
