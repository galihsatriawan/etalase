"""A shop address of its own: https://<slug>.<STORE_BASE_DOMAIN> instead of /stores/<id> on the shared host.

Optional. With STORE_BASE_DOMAIN unset nothing changes and every link stays a path. With it set, each shop can claim a
slug; the link is then shorter and easier to say aloud ("rina.etalase.id"), and each shop gets its own browser origin
(scripts, storage and forms of one shop can never touch another shop's page).

Needs wildcard DNS (*.<domain> to this service) and a wildcard certificate: see SETUP.md. The Host header is mapped to a
shop in gateway/hosts.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..models import now

if TYPE_CHECKING:
    from ..config import Settings
    from ..container import AppContext
    from ..models import Seller

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")    # no leading, trailing or double hyphen ("xn--" lookalikes are refused below)
MIN_LEN, MAX_LEN = 3, 30                              # a DNS label may be 63 characters; shorter reads better
RESERVED = frozenset({
    "www", "api", "app", "admin", "dashboard", "mcp", "ucp", "static", "assets", "cdn", "docs", "help", "support", "status",
    "mail", "smtp", "ftp", "ns1", "ns2", "blog", "dev", "test", "staging", "webhooks", "tasks", "connector", "auth", "agent",
    "pay", "checkout", "shop", "store", "stores", "etalase", "root", "billing", "login", "signup", "security",
})


# A shop that moves to a new address keeps the old one reserved for this long. Old links, QR codes and printed flyers still
# point at it, and the next person to take it would receive that traffic. The previous owner can take it back at any time.
RELEASE_COOLDOWN = timedelta(days=30)


class SlugError(ValueError):
    pass


def normalise(raw: str) -> str:
    slug = (raw or "").strip().lower()
    if not MIN_LEN <= len(slug) <= MAX_LEN:
        raise SlugError(f"Use {MIN_LEN} to {MAX_LEN} characters.")
    if not SLUG_RE.fullmatch(slug) or slug.startswith("xn"):
        raise SlugError("Use only lowercase letters, numbers and single hyphens, starting and ending with a letter or number.")
    if slug in RESERVED:
        raise SlugError("That name is reserved. Choose another.")
    return slug


def _is_platform_label(ctx: "AppContext", slug: str) -> bool:
    """A name equal to the label of the platform's own host (say 'console' on console.example.com) would serve the
    dashboard, not the shop."""
    s = ctx.settings
    return bool(s.store_base_domain) and canonical_host(s, f"{slug}.{s.store_base_domain}") == canonical_host(s, urlparse(s.public_base_url).netloc)


def slugify(name: str) -> str:
    """A first suggestion from the shop name: 'Toko Rina Hijab!' -> 'toko-rina-hijab'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:MAX_LEN].strip("-")
    return slug if len(slug) >= MIN_LEN else (slug + "-shop").strip("-")


def claim(ctx: "AppContext", seller_id: str, raw: str) -> "Seller":
    """Gives the shop this slug if it is valid and free. The old address stops working, so the dashboard says so.

    Checking "is it free" and then saving is not enough: two shops claiming the same name at once would both pass the check.
    So the address is also an entry in a small registry that the store creates atomically; exactly one claim wins."""
    slug = normalise(raw)
    if _is_platform_label(ctx, slug):
        raise SlugError("That name is reserved. Choose another.")
    seller = ctx.store.get_seller(seller_id)
    if seller.slug == slug:
        return seller
    owner = ctx.store.find_seller_by_slug(slug)      # also covers shops that claimed an address before the registry existed
    if owner is not None and owner.id != seller_id:
        raise SlugError("That address is taken. Choose another.")
    if not _reserve(ctx, slug, seller_id):
        raise SlugError("That address is taken. Choose another.")
    previous, seller.slug = seller.slug, slug
    ctx.store.put_seller(seller)
    if previous:
        _release(ctx, previous, seller_id)
    return seller


def _reserve(ctx: "AppContext", slug: str, seller_id: str) -> bool:
    if ctx.store.create_doc("slug", slug, {"seller_id": seller_id, "released_at": None}):
        return True
    won: dict[str, bool] = {}

    def take(d: dict) -> bool:
        won.clear()      # Firestore may run a transaction callback more than once
        released = datetime.fromisoformat(d["released_at"]) if d.get("released_at") else None
        mine = d.get("seller_id") == seller_id
        if not mine and (released is None or now() - released < RELEASE_COOLDOWN):
            return False     # held by another shop, or released too recently to hand to a stranger
        d["seller_id"], d["released_at"] = seller_id, None
        won["yes"] = True
        return True
    ctx.store.update_doc("slug", slug, take)
    return bool(won)


def _release(ctx: "AppContext", slug: str, seller_id: str) -> None:
    def release(d: dict) -> bool:
        if d.get("seller_id") != seller_id or d.get("released_at"):
            return False
        d["released_at"] = now().isoformat()
        return True
    ctx.store.update_doc("slug", slug, release)


def assign(ctx: "AppContext", seller_id: str) -> "Seller":
    """First free slug from the shop name (rina, rina-2, rina-3, ...). Used when a shop is created."""
    seller = ctx.store.get_seller(seller_id)
    base = slugify(seller.name)
    for n in range(1, 50):
        candidate = base if n == 1 else f"{base[:MAX_LEN - len(str(n)) - 1]}-{n}"
        try:
            return claim(ctx, seller_id, candidate)
        except SlugError:
            continue
    return seller


def subdomains_enabled(s: "Settings") -> bool:
    return bool(s.store_base_domain)


def store_origin(s: "Settings", seller: "Seller") -> str:
    """Where this shop lives: its own subdomain when it has a slug and a base domain is set, else the shared host."""
    if s.store_base_domain and seller.slug:
        scheme = "https" if s.public_base_url.startswith("https") else "http"
        return f"{scheme}://{seller.slug}.{s.store_base_domain}"
    return s.public_base_url.rstrip("/")


def store_url(s: "Settings", seller: "Seller") -> str:
    """The link a seller shares: the subdomain root, or /stores/<id> on the shared host."""
    if s.store_base_domain and seller.slug:
        return store_origin(s, seller)
    return f"{s.public_base_url.rstrip('/')}/stores/{seller.id}"


def canonical_host(s: "Settings", host: str) -> str:
    """The Host header in one comparable form: lowercase, no trailing dot ("rina.example.com." is the same host as
    "rina.example.com"), and the port only when STORE_BASE_DOMAIN itself has one (a local run on rina.localhost:8080).
    A public deployment is reached on 443 whatever port the client writes into the header, so the port means nothing there."""
    name, _, port = (host or "").strip().lower().partition(":")
    name = name.rstrip(".")
    return f"{name}:{port}" if port and ":" in (s.store_base_domain or "") else name


def is_shop_host(s: "Settings", host: str) -> bool:
    """True for anything under the base domain except the platform's own host (and the bare domain itself): those hosts
    belong to shops, so a name nobody claimed must answer 404 and never fall through to the dashboard."""
    base = (s.store_base_domain or "").lower()
    host = canonical_host(s, host)
    if not base or not host.endswith("." + base):
        return False
    return host != canonical_host(s, urlparse(s.public_base_url).netloc)


def slug_from_host(s: "Settings", host: str) -> str | None:
    """'rina.etalase.id' (or with a port, or a trailing dot) -> 'rina'. None for the platform host, the bare domain, a name
    with more than one label ('a.b.etalase.id') or anything outside the base domain."""
    if not is_shop_host(s, host):
        return None
    label = canonical_host(s, host)[: -len((s.store_base_domain or "")) - 1]
    return label if "." not in label and label else None
