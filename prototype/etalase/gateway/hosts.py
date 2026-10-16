"""Maps a shop's own address to its store page.

With STORE_BASE_DOMAIN set, a request for rina.<domain>/ is served as /stores/<id> of the shop that claimed "rina".
Every other path keeps working on that host (product pages, checkout, pay pages, static files), so no link has to change.
Any host under the base domain that is not the platform host answers 404 unless a shop claimed it (also with a trailing dot or
a port in the header), so wildcard DNS never exposes the dashboard.
The dashboard and the API stay on the platform host, which is where "/" shows the sign-in page.
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..seller.slugs import is_shop_host, slug_from_host


class ShopHostMiddleware:
    def __init__(self, app: ASGIApp, ctx) -> None:
        self.app, self.ctx = app, ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.ctx.settings.store_base_domain:
            return await self.app(scope, receive, send)
        host = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"host"), "")
        if not is_shop_host(self.ctx.settings, host):    # the platform host, the bare domain, or another domain
            return await self.app(scope, receive, send)
        slug = slug_from_host(self.ctx.settings, host)
        seller = self.ctx.store.find_seller_by_slug(slug) if slug else None
        if seller is None:     # includes names nobody claimed and names with several labels: never the dashboard
            return await PlainTextResponse("There is no shop at this address.", status_code=404)(scope, receive, send)
        if scope["path"] in ("", "/"):
            path = f"/stores/{seller.id}"
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)
