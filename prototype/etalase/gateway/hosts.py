"""Maps a shop's own address to its store page.

With STORE_BASE_DOMAIN set, a request for rina.<domain>/ is served as /stores/<id> of the shop that claimed "rina".
Every other path keeps working on that host (product pages, checkout, pay pages, static files), so no link has to change.
An address nobody claimed answers 404 instead of falling through to the dashboard, so wildcard DNS never exposes it.
The dashboard and the API stay on the platform host, which is where "/" shows the sign-in page.
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..seller.slugs import slug_from_host


class ShopHostMiddleware:
    def __init__(self, app: ASGIApp, ctx) -> None:
        self.app, self.ctx = app, ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.ctx.settings.store_base_domain:
            return await self.app(scope, receive, send)
        host = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"host"), "")
        slug = slug_from_host(self.ctx.settings, host)
        if slug is None:
            return await self.app(scope, receive, send)
        seller = self.ctx.store.find_seller_by_slug(slug)
        if seller is None:
            return await PlainTextResponse("There is no shop at this address.", status_code=404)(scope, receive, send)
        if scope["path"] in ("", "/"):
            path = f"/stores/{seller.id}"
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)
