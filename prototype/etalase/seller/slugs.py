"""A shop address of its own: https://<slug>.<STORE_BASE_DOMAIN> instead of /stores/<id> on the shared host.

Optional. With STORE_BASE_DOMAIN unset nothing changes and every link stays a path. With it set, each shop can claim a
slug; the link is then shorter and easier to say aloud ("rina.etalase.id"), and each shop gets its own browser origin
(scripts, storage and forms of one shop can never touch another shop's page).

Needs wildcard DNS (*.<domain> to this service) and a wildcard certificate: see SETUP.md. The Host header is mapped to a
shop in gateway/hosts.py.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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


def slugify(name: str) -> str:
    """A first suggestion from the shop name: 'Toko Rina Hijab!' -> 'toko-rina-hijab'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:MAX_LEN].strip("-")
    return slug if len(slug) >= MIN_LEN else (slug + "-shop").strip("-")


def claim(ctx: "AppContext", seller_id: str, raw: str) -> "Seller":
    """Gives the shop this slug if it is valid and free. The old address stops working, so the dashboard says so."""
    slug = normalise(raw)
    owner = ctx.store.find_seller_by_slug(slug)
    if owner is not None and owner.id != seller_id:
        raise SlugError("That address is taken. Choose another.")
    seller = ctx.store.get_seller(seller_id)
    seller.slug = slug
    ctx.store.put_seller(seller)
    return seller


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


def slug_from_host(s: "Settings", host: str) -> str | None:
    """'rina.etalase.id' (or with a port) -> 'rina'. None for the main host, the bare domain or anything else."""
    base = (s.store_base_domain or "").lower()
    host = host.strip().lower()
    if not base or not host.endswith("." + base) or host == urlparse(s.public_base_url).netloc.lower():
        return None     # the platform's own host (say app.example.com under example.com) is never a shop
    label = host[: -len(base) - 1]
    return label if "." not in label and label else None
