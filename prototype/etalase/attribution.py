"""Where an order came from, as a coarse label.

We want to know which doors buyers actually use (the store link opened from an AI assistant, an Instagram bio, a WhatsApp
status, an agent calling MCP or UCP) before we decide what to build next. So every order records:

    channel   how it was placed: web (the store link), mcp, ucp or rest (an agent)
    referrer  who sent the buyer: "chatgpt", "gemini", "instagram", "direct", or the name of the calling agent

Rules that keep this harmless:
  * a label, never a URL, a query string, an IP address or anything else that could point at a person;
  * the label comes from a fixed list when it is a site (anything else is "other"), and from a sanitised slug when it is an
    agent's own name, clipped to 40 characters;
  * the web label travels in one first-party cookie that holds only the label, lasts 30 minutes and never identifies anyone.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

CHANNELS = ("web", "mcp", "ucp", "rest")
COOKIE = "etl_src"
COOKIE_SECONDS = 30 * 60
MAX_LABEL = 40

# label -> the hosts (and their subdomains) that count as that source. Order matters only for readability.
SITES = {
    "chatgpt": ("chatgpt.com", "chat.openai.com", "openai.com"),
    "gemini": ("gemini.google.com", "bard.google.com", "aistudio.google.com"),
    "claude": ("claude.ai",),
    "perplexity": ("perplexity.ai",),
    "copilot": ("copilot.microsoft.com", "bing.com"),
    "google": ("google.com", "google.co.id"),
    "instagram": ("instagram.com", "l.instagram.com"),
    "facebook": ("facebook.com", "fb.com", "fb.me", "messenger.com"),
    "whatsapp": ("whatsapp.com", "wa.me"),
    "tiktok": ("tiktok.com",),
    "telegram": ("t.me", "telegram.org"),
    "x": ("x.com", "twitter.com", "t.co"),
}
# `utm_source=chatgpt.com` style values are matched against the same table, plus these short names.
ALIASES = {"ig": "instagram", "fb": "facebook", "wa": "whatsapp", "tg": "telegram", "openai": "chatgpt", "bard": "gemini"}

_SLUG = re.compile(r"[^a-z0-9._-]+")


def slug(text: str | None) -> str:
    """Lower-case letters, digits, dot, dash and underscore only; anything else becomes a dash."""
    return _SLUG.sub("-", (text or "").strip().lower()).strip("-.")[:MAX_LABEL]


def _site_label(host: str) -> str:
    host = host.lower().strip(".")
    for label, hosts in SITES.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return label
    return ""


def from_referer(referer: str | None, own_hosts: tuple[str, ...] = ()) -> str:
    """The label for a Referer header, "" when there is none or it is our own site. Only the host is ever looked at."""
    try:
        host = (urlsplit((referer or "").strip()[:2000]).hostname or "").lower()
    except ValueError:
        return ""
    if not host:
        return ""
    if any(h and (host == h or host.endswith("." + h)) for h in own_hosts):
        return ""      # moving around our own pages is not a new source
    return _site_label(host) or "other"


def from_param(value: str | None) -> str:
    """The label for `?src=` or `?utm_source=`: a known site or alias by name, or a short slug a seller chose for their own
    link ("ig-bio", "wa-status"), which is what lets a seller tell their own channels apart."""
    raw = slug(value)
    if not raw:
        return ""
    return ALIASES.get(raw) or _site_label(raw) or raw


def landing_source(query: dict[str, str] | None, referer: str | None, own_hosts: tuple[str, ...] = ()) -> str:
    """The source of a visit to the store link. A parameter on the link beats the Referer header, because many apps (WhatsApp,
    Instagram's in-app browser) send no Referer at all while a link the seller or an assistant built can say where it was shared."""
    q = query or {}
    return from_param(q.get("src") or q.get("utm_source")) or from_referer(referer, own_hosts)


def agent_from_user_agent(user_agent: str | None) -> str:
    """The product name at the start of a User-Agent ("claude-user/1.0 (...)" -> "claude-user"). Best effort: an MCP client
    behind our stateless transport does not always say who it is."""
    first = (user_agent or "").strip().split(" ", 1)[0].split("/", 1)[0]
    return slug(first)


def agent_from_ucp_header(header: str | None) -> str:
    """The host of the platform profile in `UCP-Agent: profile="https://platform.example/profile"`. Only the host is kept."""
    m = re.search(r'profile="([^"]*)"', header or "")
    if not m:
        return ""
    try:
        return slug(urlsplit(m.group(1)).hostname or "")
    except ValueError:
        return ""


def clean_cookie(value: str | None) -> str:
    """A cookie is user-controlled input like any other: accept only something that looks like one of our labels."""
    label = slug(value)
    return label if label and label == (value or "") else ""
