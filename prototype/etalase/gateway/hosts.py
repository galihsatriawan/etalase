"""Maps a shop's own address to its store page, and scopes everything an agent can reach on that address to that shop.

With STORE_BASE_DOMAIN set, a request for rina.<domain>/ is served as /stores/<id> of the shop that claimed "rina".
Every other path keeps working on that host (product pages, checkout, pay pages, static files), so no link has to change.
Any host under the base domain that is not the platform host answers 404 unless a shop claimed it (also with a trailing dot or
a port in the header), so wildcard DNS never exposes the dashboard.
The dashboard and the API stay on the platform host, which is where "/" shows the sign-in page.

A shop's address is also its own business as far as agents are concerned (UCP describes a business by the profile on its
domain): /.well-known/ucp, /ucp/v1, /agent/v1 and /mcp on rina.<domain> only see Rina's products, and the links in their
answers point back at rina.<domain>. The middleware resolves the shop once and puts a ShopScope in the request state, so the
routes below never parse the Host header themselves.

A shop that is not verified yet shows a holding page on its address and nothing else: no products, no agent endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..seller.slugs import is_shop_host, slug_from_host, store_origin


@dataclass(frozen=True)
class ShopScope:
    seller_id: str
    origin: str          # https://rina.<domain>, no trailing slash


def shop_of(request_or_scope) -> ShopScope | None:
    """The shop whose address this request came in on, or None on the platform host. Set only by ShopHostMiddleware, from the
    Host header it resolved, so a client cannot choose it any other way."""
    scope = getattr(request_or_scope, "scope", request_or_scope)
    return (scope.get("state") or {}).get("shop")


# On the address of a shop that is not verified, only these paths answer.
HOLDING_PATHS = ("/static/",)


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
        path = scope["path"]
        if not seller.open_for_buyers and path != "/" and not path.startswith(HOLDING_PATHS):
            return await PlainTextResponse("This shop is not open yet.", status_code=404)(scope, receive, send)
        parts = path.split("/", 3)       # "/stores/<id>/..." on rina.<domain> must be Rina's, or the address would show another shop
        if len(parts) > 2 and parts[1] == "stores" and parts[2] != seller.id:
            return await PlainTextResponse("There is no shop at this address.", status_code=404)(scope, receive, send)
        state = {**(scope.get("state") or {}), "shop": ShopScope(seller.id, store_origin(self.ctx.settings, seller))}
        scope = {**scope, "state": state}
        if path in ("", "/"):
            path = f"/stores/{seller.id}"
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)
