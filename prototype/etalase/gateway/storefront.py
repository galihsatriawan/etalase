"""The public store link: one page per seller that people and AI agents can both use.

    GET  /stores/{seller_id}                     the catalog (schema.org JSON-LD inside, pointers to MCP and UCP)
    GET  /stores/{seller_id}/p/{product_id}      one product: photos, options, seller, return protection, a "Buy now" bar
    GET  /stores/{seller_id}/buy/{variant_id}    checkout: quantity, contact and delivery details, payment method
    POST /stores/{seller_id}/buy/{variant_id}    creates the same checkout an agent would, then goes to the pay page
    POST /stores/{seller_id}/cart/{add|update|remove}   one cart per shop, kept in a cookie (variant ids and quantities only)
    GET  /stores/{seller_id}/cart/badge          the cart button alone (the page script refreshes it on Back)
    GET  /stores/{seller_id}/cart                the cart: lines, subtotal, a way to change or remove each line
    GET/POST /stores/{seller_id}/cart/checkout   one order for the whole cart (all or nothing, one shop), then the pay page
    GET  /pay/{order_id}                         QRIS code, then the order's status (the order id is the secret)
    GET/POST /pay/{order_id}/return              a return request for a person (the phone number is the proof, like for agents)

It is deliberately server-rendered HTML with no framework and no new dependency: it works in an Instagram bio, a WhatsApp status
or a QR code on a parcel, and the order lands in the seller's WhatsApp exactly as an agent's order does. Everything a seller or a
buyer typed is escaped; the pages send a strict Content-Security-Policy (one hash-pinned script, no other source) and are never
cached. The look, and the one small script, live in `pages.py`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from fastapi.responses import HTMLResponse, RedirectResponse

from .. import demo
from ..models import Address, CheckoutError, Order, OrderStatus, Product, now
from ..seller import slugs
from ..seller.format import rupiah
from . import discovery, pages
from .pages import layout

if TYPE_CHECKING:
    from ..container import AppContext

STORE_CSP = pages.csp(images="'self' https: data:", forms=True)
MAX_FORM_BYTES = 8_000
FIELD_LIMITS = {"name": 80, "phone": 20, "address": 200, "city": 60, "postal": 10, "note": 500}
STEPS = ["Your details", "Payment", "Shipping", "Delivered"]


def _respond(html: str, status: int = 200) -> HTMLResponse:
    # "same-origin", not the API-wide "no-referrer": with no-referrer a browser sends "Origin: null" on the form posts of these
    # pages and our own cross-site check rejects every real purchase. Other sites still never see a store or order URL.
    return HTMLResponse(html, status_code=status, headers={"Content-Security-Policy": STORE_CSP, "Cache-Control": "no-store",
                                                           "Referrer-Policy": "same-origin"})


def _not_found(what: str, back: tuple[str, str] | None = None) -> HTMLResponse:
    return _respond(layout("Not found", f'<section class="stat info"><span class="bubble info">{pages.ico("search")}</span><h2>{escape(what)}</h2>'
                           '<div class=sub><p>Check the link you were given, or ask the seller to send it again.</p></div></section>', back=back), 404)


def _jsonld(data) -> str:
    # "<" is escaped so a seller's text can never close the script element.
    return '<script type="application/ld+json">' + json.dumps(data).replace("<", "\\u003c") + "</script>"


def _open(ctx: "AppContext", seller_id: str) -> bool:
    seller = ctx.store.get_seller(seller_id)
    return seller is not None and seller.open_for_buyers


def _find(ctx: "AppContext", seller_id: str, variant_id: str) -> tuple[Product, object] | None:
    if not _open(ctx, seller_id):     # a shop still being set up has nothing to buy
        return None
    for p in ctx.store.list_products(seller_id=seller_id, status="published"):
        for v in p.variants:
            if v.id == variant_id:
                return p, v
    return None


def parse_form(raw: bytes) -> dict[str, str]:
    """application/x-www-form-urlencoded, with every field clipped to its limit. No multipart, so no extra dependency."""
    if len(raw) > MAX_FORM_BYTES:
        raise CheckoutError("the form is too large")
    fields = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: (v[0] if v else "")[:FIELD_LIMITS.get(k, 40)] for k, v in fields.items()}


def _label(options: dict) -> str:
    return " / ".join(str(x) for x in options.values())


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _img_src(ctx: "AppContext", path: str) -> str:
    """What the page's <img> loads. A photo we host is addressed on the page's own origin (/media/<file>): the page may be a
    shop address (rina.<domain>) and the CSP only allows 'self' and https, so the absolute public URL would be blocked
    over plain http (a local try). Photos hosted elsewhere keep their own URL. Meta tags and JSON-LD keep absolute URLs."""
    if path.startswith(("https://", "http://")):
        return ctx.catalog.image_url(path)
    return "/media/" + os.path.basename(path)


def _media(ctx: "AppContext", product: Product, *, hero: bool = False, index: int = 0) -> str:
    """A photograph, or a quiet tile with the product's first letter when the seller has not sent one yet."""
    if product.images and index < len(product.images):
        url = _img_src(ctx, product.images[index])
        alt = product.title + (f" (photo {index + 1})" if index else "")
        return (f'<img src="{escape(url)}" alt="{escape(alt)}" {"" if hero else "loading=lazy "}decoding=async width=800 height=1000>')
    return f'<div class=ph aria-hidden=true>{escape(pages.initial(product.title))}</div>'


def _adds_information(text: str, title: str, labels: list[str]) -> bool:
    """False for a description that only repeats the title and the option names ("Pashmina Silk, available in Sage, Lilac.")."""
    rest = text.lower()
    for known in [title, "available in", *labels]:
        rest = rest.replace(known.lower(), " ")
    return sum(ch.isalnum() for ch in rest) >= 12


def _option_name(variants: list[dict]) -> str:
    """"Color" when every variant differs by one option called that; otherwise a plain "Options"."""
    keys = {k for v in variants for k in v["options"]}
    return next(iter(keys)).replace("_", " ").title() if len(keys) == 1 else "Options"


def _from_price(prices: list[int]) -> str:
    if not prices:
        return ""
    lead = "<small>from</small>" if len(prices) > 1 else ""
    return f"<p class=price>{lead}{escape(rupiah(prices[0]))}</p>"


# Colour names (English and Indonesian) that a seller is likely to use for an option, so a card can show dots instead of words.
_SWATCH = {
    "black": "#1b1b1b", "hitam": "#1b1b1b", "white": "#f4f4f1", "putih": "#f4f4f1", "cream": "#f3ead2", "krem": "#f3ead2", "ivory": "#f6f1e1",
    "beige": "#d9c5a0", "navy": "#1f2f5c", "olive": "#6b7a3a", "maroon": "#7a1f2b", "burgundy": "#6e1423", "pink": "#f0a6b8", "rose": "#e8a0b0",
    "blue": "#3b6fd0", "biru": "#3b6fd0", "sky": "#8ec5ea", "green": "#2f8f5b", "hijau": "#2f8f5b", "mint": "#a9dcc3", "sage": "#9caf88",
    "red": "#c0392b", "merah": "#c0392b", "yellow": "#f2c230", "kuning": "#f2c230", "mustard": "#d9a21b", "orange": "#ee8a2a", "oranye": "#ee8a2a",
    "peach": "#f6b99a", "grey": "#9aa0a6", "gray": "#9aa0a6", "abu": "#9aa0a6", "silver": "#c0c4c8", "perak": "#c0c4c8", "brown": "#7a5230",
    "coklat": "#7a5230", "cokelat": "#7a5230", "mocha": "#8b6b55", "mocca": "#8b6b55", "moka": "#8b6b55", "coffee": "#6f4e37", "tan": "#c8a27a", "khaki": "#bda57a", "purple": "#7b52b5",
    "ungu": "#7b52b5", "lavender": "#b9a7e0", "lilac": "#c8a2c8", "gold": "#d4af37", "emas": "#d4af37", "taupe": "#a89484", "terracotta": "#c4684a", "nude": "#e3bfa6", "natural": "#d8c7a6", "sand": "#d8c7a6", "stone": "#b9b3a6", "charcoal": "#3a3f3d", "teal": "#1f8a8a", "coral": "#f26d5b", "salmon": "#f4a08c", "denim": "#4a6fa5", "dusty": "#c9a3a6",
}


def _swatches(labels: list[str]) -> str:
    """Colour dots for a card, only when every option name holds a colour word and there are at least two different colours."""
    seen: list[str] = []
    for text in labels:
        words = re.split(r"[\s/_\-,]+", text.lower())
        hexv = next((_SWATCH[w] for w in words if w in _SWATCH), None)
        if hexv is None:
            return ""
        if hexv not in seen:
            seen.append(hexv)
    if len(seen) < 2:
        return ""
    dots = "".join(f'<i style="background:{c}"></i>' for c in seen[:5])
    more = f"<span>+{len(seen) - 5}</span>" if len(seen) > 5 else ""
    return f'<p class=sw aria-hidden=true>{dots}{more}</p>'


# ------------------------------------------------------------------ the catalog
def _product_card(ctx: "AppContext", seller, p: Product, index: int = 0, *, rise: bool = True) -> str:
    view = ctx.catalog.agent_view(p, seller)
    variants = view["variants"]
    labels = [_label(v["options"]) for v in variants if v["options"]]
    prices = sorted({v["price"] for v in variants})
    flag = "" if variants else "<span class=flag>Sold out</span>"
    dots = _swatches(labels)
    opts = dots or (f'<p class=opts>{escape(" · ".join(labels))}</p>' if len(labels) > 1 or (labels and labels[0]) else "")
    confirm = "<p class=hold>Seller confirms stock</p>" if variants and all(v["availability"] == "confirm_with_seller" for v in variants) else ""
    inner = (f'<div class=media style="{pages.tone_style(p.id)}">{_media(ctx, p)}{flag}</div>'
             f'<div class=body><h3 class=name>{escape(p.title)}</h3>{opts}{_from_price(prices)}{confirm}</div>')
    cls = "pc rise" if rise else "pc"
    data = (f'data-i="{index}" data-price="{prices[0] if prices else 0}" '
            f'data-name="{escape((p.title + " " + " ".join(labels)).lower())}" style="--i:{min(index, 8)}"')
    if not variants:
        return f'<article class="{cls}" {data}>{inner}</article>'
    return (f'<a class="{cls}" {data} href="/stores/{escape(seller.id)}/p/{escape(p.id)}" '
            f'aria-label="{escape(p.title)}, {escape(rupiah(prices[0])) if prices else ""}">{inner}</a>')


SHARE = f'<button class="iconbtn end" type=button data-share aria-label="Share this page">{pages.ico("share")}</button>'


def _holding_page() -> HTMLResponse:
    """What anyone sees at the address of a shop that is not verified yet. It names nothing: no shop name, no products."""
    return _respond(layout("Opening soon", '<section class="stat info"><span class="bubble info">' + pages.ico("clock") + '</span>'
                           '<h2>This shop is not open yet</h2><div class=sub><p>It is being set up. Check back soon.</p></div></section>'),
                    404)


def store_page(ctx: "AppContext", seller_id: str, cart_raw: str = "") -> HTMLResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        return _not_found("This store was not found.")
    if not seller.open_for_buyers:
        return _holding_page()
    base = slugs.store_origin(ctx.settings, seller)      # on a shop's own address the agent endpoints are on that address too
    sid = escape(seller.id)
    products = ctx.store.list_products(seller_id=seller.id, status="published")
    city = seller.origin.city if seller.origin else ""
    done = ctx.checkout.track_record(seller.id)["orders_completed"]
    policy = ctx.returns.policy_view(seller)
    where = f'<p class=where>{pages.ico("pin", "sm")}{escape(city)}</p>' if city else ""
    look = pages.tone_style(seller.id)
    if policy["window_hours"]:
        line = (f'{pages.ico("shield", "sm")}<span>Returns within {escape(pages.window(policy["window_hours"]))}. Payment is held until delivery. '
                f'<a href="/stores/{sid}/returns">Policy</a></span>')
        returns_stat = escape(pages.window(policy["window_hours"]))
    else:
        line = f'{pages.ico("alert", "sm")}<span>No returns through Etalase. <a href="/stores/{sid}/returns">Details</a></span>'
        returns_stat = "None"
    third = (f"<b>{done}</b><span>Orders done</span>" if done else
             (f"<b>{escape(city)}</b><span>Ships from</span>" if city else "<b>QRIS</b><span>Payment</span>"))
    head_block = (f'<div class="cover rise" style="{look}" aria-hidden=true></div>'
                  f'<div class="shop rise"><span class=avatar style="{look}" aria-hidden=true>{escape(pages.initial(seller.name))}</span>'
                  f'<div class=id><h1>{escape(seller.name)}</h1>{where}</div></div>'
                  f'<p class=policyline>{line}</p>'
                  f'<div class="stats rise" style="--i:1"><div><b>{len(products)}</b><span>Products</span></div>'
                  f'<div><b>{returns_stat}</b><span>Returns</span></div><div>{third}</div></div>')
    cards = "".join(_product_card(ctx, seller, p, i) for i, p in enumerate(products))
    count = f'<span class=count>{len(products)} item{"" if len(products) == 1 else "s"}</span>' if products else ""
    tools = ""
    if len(products) > 1:
        tools = (f'<div class=tools><div class=search>{pages.ico("search")}<input id=q type=search placeholder="Search this store" '
                 'aria-label="Search this store" autocomplete=off enterkeyhint=search></div>'
                 '<div class=sorts role=group aria-label="Sort products"><button type=button data-sort=new aria-pressed=true>Newest</button>'
                 '<button type=button data-sort=asc aria-pressed=false>Price: low to high</button>'
                 '<button type=button data-sort=desc aria-pressed=false>Price: high to low</button></div></div>')
    grid = (f'<div class=sec><h2>Products</h2>{count}</div>{tools}<div class=grid>{cards}</div>'
            '<p id=none class=note hidden>No product matches your search.</p>' if cards else
            '<section class="band" style="margin-top:1rem"><h2>No products yet</h2><p class=note>This store has not published anything yet. Check back soon.</p></section>')
    agents = (f'<details class=devnote><summary>{pages.ico("code", "sm")}For AI shopping assistants{pages.ico("chevron", "sm")}</summary>'
              '<p>Agents can read and buy from this store with the same stock and return rules as this page.</p><dl>'
              f'<dt>MCP</dt><dd><code>{escape(base)}/mcp/</code></dd>'
              f'<dt>UCP profile</dt><dd><code>{escape(base)}/.well-known/ucp</code></dd>'
              f'<dt>Product data</dt><dd><a href="/stores/{sid}/products.jsonld">schema.org JSON-LD</a></dd></dl></details>')
    first_image = next((ctx.catalog.image_url(p.images[0]) for p in products if p.images), "")
    summary = f"Order from {seller.name}{' in ' + city if city else ''}. Pay with QRIS; the seller ships by courier."
    head = (f'<meta name=description content="{escape(summary)}"><meta property=og:type content=website>'
            f'<meta property=og:title content="{escape(seller.name)}"><meta property=og:description content="{escape(summary)}">'
            f'<meta property=og:url content="{escape(slugs.store_url(ctx.settings, seller))}">'
            + (f'<meta property=og:image content="{escape(first_image)}">' if first_image else "")
            + f'<link rel="alternate" type="application/ld+json" href="{escape(base)}/stores/{sid}/products.jsonld">'
            + _jsonld(discovery.schema_org_products(ctx, seller.id)))
    return _respond(layout(seller.name, head_block + grid + agents, head=head, index=True, wide=True, heading=False, flush=True,
                           action=_bar_action(ctx, seller.id, cart_raw)))


# ------------------------------------------------------------------ one product
def _product_of(ctx: "AppContext", seller_id: str, product_id: str) -> Product | None:
    if not _open(ctx, seller_id):
        return None
    return next((p for p in ctx.store.list_products(seller_id=seller_id, status="published") if p.id == product_id), None)


def product_page(ctx: "AppContext", seller_id: str, product_id: str, variant_id: str = "", cart_raw: str = "") -> HTMLResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    product = _product_of(ctx, seller.id, product_id[:64]) if seller else None
    if seller is None or product is None:
        return _not_found("This item was not found. It may have been sold out or removed.",
                          back=(f"/stores/{seller.id}", seller.name) if seller else None)
    sid, pid = escape(seller.id), escape(product.id)
    view = ctx.catalog.agent_view(product, seller)
    shown = view["variants"]
    current = (next((v for v in shown if v["variant_id"] == variant_id[:64]), None)
               or next((v for v in shown if v["availability"] != "confirm_with_seller"), None) or (shown[0] if shown else None))
    prices = sorted({v["price"] for v in shown})
    policy = ctx.returns.policy_view(seller)
    city = seller.origin.city if seller.origin else ""
    look = pages.tone_style(product.id)

    n = max(1, min(len(product.images), 8))
    slides = "".join(f'<div class=slide><div class=media style="{look}">{_media(ctx, product, hero=(i == 0), index=i)}</div></div>' for i in range(n))
    nav = ""
    if n > 1:
        nav = f'<span class=cnt aria-hidden=true>1/{n}</span><span class=dotsnav aria-hidden=true>' + "".join(f'<i{" class=on" if i == 0 else ""}></i>' for i in range(n)) + "</span>"
    gallery = f'<section class=gallery aria-label="Photos"><div class=slides tabindex=0>{slides}</div>{nav}</section>'

    if current is None:
        price_html = '<p class=pricebig>Sold out</p>'
        tag = '<span class="tag wait">Sold out</span>'
    else:
        price_html = f'<p class=pricebig data-price-text>{escape(rupiah(current["price"]))}</p>'
        waits = current["availability"] == "confirm_with_seller"
        tag = f'<span class="tag{" wait" if waits else ""}" data-avail="{escape(current["availability"])}">{"Seller confirms stock" if waits else "Ready stock"}</span>'
    main_band = f'<section class=band>{price_html}<h1>{escape(product.title)}</h1><div class=meta>{tag}</div></section>'

    chips = ""
    if current is not None and (len(shown) > 1 or _label(current["options"])):
        parts = []
        for o in shown:
            label = _label(o["options"]) or "Default"
            extra = f' <small>{escape(rupiah(o["price"]))}</small>' if len(prices) > 1 else ""
            on = o["variant_id"] == current["variant_id"]
            vid = escape(o["variant_id"])
            parts.append(f'<a class="chip{" on" if on else ""}"{" aria-current=true" if on else ""} href="/stores/{sid}/p/{pid}?v={vid}" data-id="{vid}" '
                         f'data-vprice="{o["price"]}" data-vlabel="{escape(label)}" data-vavail="{escape(o["availability"])}" data-buy="/stores/{sid}/buy/{vid}">'
                         f'{escape(label)}{extra}</a>')
        chips = (f'<section class="band opt"><h2 class=cap>{escape(_option_name(shown))}: <span data-label>{escape(_label(current["options"]) or "Default")}</span></h2>'
                 f'<div class=chips data-variants>{"".join(parts)}</div></section>')

    if policy["window_hours"]:
        trust = (f'<span class=badge>{pages.ico("shield")}</span><div><b>Payment protected</b>'
                 f'<p>You pay by QRIS. {escape(seller.name)} is paid only after delivery and the {escape(pages.window(policy["window_hours"]))} return window.</p>'
                 f'<a href="/stores/{sid}/returns">See return policy</a></div>')
    else:
        trust = (f'<span class=badge>{pages.ico("alert")}</span><div><b>No returns through Etalase</b>'
                 f'<p>Check the item details before you order.</p><a href="/stores/{sid}/returns">Details</a></div>')
    trust_band = f'<section class="band trust">{trust}</section>'

    seller_band = (f'<section class=band><div class=sellerrow><span class="avatar sm" style="{pages.tone_style(seller.id)}" aria-hidden=true>{escape(pages.initial(seller.name))}</span>'
                   f'<div class=who><b>{escape(seller.name)}</b>' + (f'<small>{pages.ico("pin", "sm")}{escape(city)}</small>' if city else "")
                   + f'</div><a class="btn alt sm" href="/stores/{sid}">Visit store</a></div></section>')

    labels = [_label(o["options"]) for o in shown]
    text = (product.description or "").strip()
    about = ""
    if text and _adds_information(text, product.title, labels):
        about = f'<section class=band><h2 class=cap>About this item</h2><p class=about>{escape(text[:900])}{"…" if len(text) > 900 else ""}</p></section>'

    others = [p for p in ctx.store.list_products(seller_id=seller.id, status="published") if p.id != product.id][:6]
    more = ""
    if others:
        more = (f'<section class=band><h2 class=cap>More from {escape(seller.name)}</h2><div class=rail>'
                + "".join(_product_card(ctx, seller, p, i, rise=False) for i, p in enumerate(others)) + "</div></section>")

    if current is None:
        buy = '<button class="btn" type=button disabled style="flex:1">Sold out</button>'
    else:
        vid = escape(current["variant_id"])
        buy = (f'<form class=addform method=post action="/stores/{sid}/cart/add"><input type=hidden name=variant_id value="{vid}" data-variant-input>'
               '<input type=hidden name=qty value=1><button class="btn alt" type=submit>Add to cart</button></form>'
               f'<a class=btn data-go href="/stores/{sid}/buy/{vid}">Buy now</a>')
    dock = f'<div class=dock><a class=iconbtn style="box-shadow:inset 0 0 0 1.5px var(--line-2)" href="/stores/{sid}" aria-label="Visit {escape(seller.name)}">{pages.ico("store")}</a>{buy}</div>'

    first_image = ctx.catalog.image_url(product.images[0]) if product.images else ""
    base = ctx.settings.public_base_url.rstrip("/")
    blurb = f"{product.title} from {seller.name}{' in ' + city if city else ''}. Pay with QRIS."
    head = (f'<meta name=description content="{escape(blurb)}"><meta property=og:type content=product>'
            f'<meta property=og:title content="{escape(product.title)}"><meta property=og:description content="{escape(blurb)}">'
            f'<meta property=og:url content="{escape(slugs.store_origin(ctx.settings, seller))}/stores/{sid}/p/{pid}">'
            + (f'<meta property=og:image content="{escape(first_image)}">' if first_image else ""))
    body = f'<div class=pdp>{gallery}<div class=info>{main_band}{chips}{dock}{trust_band}{seller_band}{about}{more}</div></div>'
    return _respond(layout(f"{product.title} · {seller.name}", body, back=(f"/stores/{seller.id}", seller.name), wide=True, heading=False,
                           flush=True, action=_bar_action(ctx, seller.id, cart_raw), head=head, index=True))


# ------------------------------------------------------------------ checkout
_FIELD_WORDS = (("postal", "postal"), ("phone", "phone"), ("name", "name"), ("address", "address"), ("city", "city"), ("quantity", "qty"))


def _bad_field(error: str) -> str:
    low = error.lower()
    return next((field for word, field in _FIELD_WORDS if word in low), "")


def _details(v: dict, bad: str) -> str:
    """Contact, delivery address and payment: the three sections the one-item checkout and the cart checkout share."""
    def field(name, title, kind="text", *, hint="", auto="", extra="", default=""):
        hint_id = f"{name}-hint"
        flag = " aria-invalid=true aria-describedby=checkout-error" if name == bad else (f" aria-describedby={hint_id}" if hint else "")
        return (f'<div class="field{" bad" if name == bad else ""}"><label for={name}>{title}</label>' + (f'<span class=hint id={hint_id}>{hint}</span>' if hint else "")
                + f'<input id={name} name={name} type={kind} value="{escape(v.get(name, default))}" maxlength={FIELD_LIMITS.get(name, 3)} '
                  f'autocomplete={auto or "off"}{flag} required {extra}></div>')
    contact = ('<section class=band><h2 class=cap>Contact</h2>'
               + field("name", "Your name", auto="name")
               + field("phone", "WhatsApp or mobile number", "tel", hint="We use it to confirm your order and to verify returns.",
                       auto="tel", extra='placeholder="0857..." inputmode=tel') + '</section>')
    addr_bad = " aria-invalid=true aria-describedby=checkout-error" if bad == "address" else " aria-describedby=address-hint"
    delivery = ('<section class=band><h2 class=cap>Delivery address</h2>'
                f'<div class="field{" bad" if bad == "address" else ""}"><label for=address>Street, house number, district</label>'
                f'<textarea id=address name=address rows=3 maxlength={FIELD_LIMITS["address"]} autocomplete=street-address{addr_bad} required>{escape(v.get("address", ""))}</textarea></div>'
                '<div class=pair>' + field("city", "City", auto="address-level2")
                + field("postal", "Postal code", auto="postal-code", extra='inputmode=numeric pattern="[0-9]{5}" placeholder="40135" title="5 digits"')
                + '</div></section>')
    payment = (f'<section class=band><h2 class=cap>Payment</h2><div class=pm>{pages.ico("qr", "lg")}<div><b>QRIS</b>'
               '<small>Scan from any banking or e-wallet app on the next page. Cash on delivery is not offered.</small></div>'
               f'<span class=tick>{pages.ico("check")}</span></div></section>')
    return contact + delivery + payment


def buy_form(ctx: "AppContext", seller_id: str, variant_id: str, values: dict | None = None, error: str = "",
             status: int = 200) -> HTMLResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    found = _find(ctx, seller_id[:64], variant_id[:64]) if seller else None
    if seller is None or found is None:
        return _not_found("This item was not found. It may have been sold out or removed.",
                          back=(f"/stores/{seller.id}", "Back to the store") if seller else None)
    product, variant = found
    sid = escape(seller.id)
    shown = ctx.catalog.agent_view(product, seller)["variants"]
    view = next((v for v in shown if v["variant_id"] == variant.id), None)
    if view is None:
        return _respond(layout("Sold out", f'<section class=band><h2>{escape(product.title)} is sold out</h2>'
                               f'<p class=note style="margin:.35rem 0 1rem">Another option may still be available.</p>'
                               f'<a class="btn alt" href="/stores/{sid}/p/{escape(product.id)}">See other options</a></section>',
                               back=(f"/stores/{seller.id}/p/{product.id}", product.title), heading=False), 410)
    v = values or {}
    try:
        shown_qty = max(1, min(ctx.settings.max_qty_per_order, int(v.get("qty", "1") or "1")))
    except ValueError:
        shown_qty = 1
    ttl = ctx.settings.reservation_ttl_minutes
    bad = _bad_field(error) if error else ""
    label = _label(variant.options)
    thumb = f'<div class=media style="{pages.tone_style(product.id)}">{_media(ctx, product, hero=True)}</div>'
    qty = (f'<div class=qtyrow><label for=qty>Quantity</label><div class=stepper>'
           f'<button class=st type=button data-step=-1 aria-label="One less">{pages.ico("minus")}</button>'
           f'<input id=qty name=qty type=number value="{shown_qty}" maxlength=3 autocomplete=off required min=1 max={ctx.settings.max_qty_per_order} inputmode=numeric>'
           f'<button class=st type=button data-step=1 aria-label="One more">{pages.ico("plus")}</button></div></div>')
    item = (f'<section class=band><h2 class=cap>Your order</h2><div class=item>{thumb}<div><b>{escape(product.title)}</b>'
            + (f'<small>{escape(label)}</small>' if label else "") + f'<p class=price>{escape(rupiah(variant.price))}</p>'
            f'<a href="/stores/{sid}/p/{escape(product.id)}?v={escape(variant.id)}">Change option</a></div></div>{qty}</section>')
    details = _details(v, bad)
    confirm = (f'<div class=callout style="margin-top:1rem">{pages.ico("clock")}<span>The seller confirms stock first. '
               'The payment code appears on the next page as soon as they do.</span></div>' if view["availability"] == "confirm_with_seller" else "")
    summary = ('<section class=band><h2 class=cap>Summary</h2><ul class=lines>'
               f'<li><span>Subtotal</span><span data-subtotal>{escape(rupiah(variant.price * shown_qty))}</span></li>'
               '<li><span>Shipping</span><span>Added on the next page</span></li></ul>'
               f'<p class=fine style="margin-top:.75rem">Shipping uses the cheapest courier. Your item is held for {ttl} minutes after you place the order.</p>{confirm}</section>')
    dock = (f'<div class=dock><div class=tot><small>Subtotal</small><b data-subtotal>{escape(rupiah(variant.price * shown_qty))}</b></div>'
            '<button class=btn type=submit data-busy="Placing your order&hellip;">Place order</button></div>')
    alert = (f'<div class=alert id=checkout-error role=alert>{pages.ico("alert")}<div><b>We could not place your order</b>{escape(_sentence(error))}</div></div>'
             if error else "")
    body = (f'<form class=stack method=post data-unit="{variant.price}"><h1 class=sr>Checkout</h1>{alert}{demo.buy_notice(ctx, seller.id)}'
            f'{item}{details}{summary}{dock}</form>')
    return _respond(layout(f"Checkout · {product.title}", body, back=(f"/stores/{seller.id}/p/{product.id}?v={variant.id}", "Checkout"),
                           heading=False), status)


def _clean_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return "62" + digits[1:] if digits.startswith("0") else digits


def buy_submit(ctx: "AppContext", seller_id: str, variant_id: str, raw: bytes, referrer: str = "") -> HTMLResponse | RedirectResponse:
    try:
        form = parse_form(raw)
    except CheckoutError as e:
        return buy_form(ctx, seller_id, variant_id, error=str(e), status=413)
    found = _find(ctx, seller_id[:64], variant_id[:64])
    if found is None:
        return _not_found("This item was not found. It may have been sold out or removed.")
    product, variant = found
    try:
        qty = int(form.get("qty", "1") or "1")
    except ValueError:
        return buy_form(ctx, seller_id, variant_id, form, "Quantity must be a number.", 422)
    postal = form.get("postal", "").strip()
    if not (postal.isdigit() and len(postal) == 5):
        return buy_form(ctx, seller_id, variant_id, form, "Postal code must be 5 digits.", 422)
    buyer = Address(name=form.get("name", "").strip(), phone=_clean_phone(form.get("phone", "")),
                    line1=" ".join(form.get("address", "").split()), city=form.get("city", "").strip(),
                    postal_code=postal, country="ID")
    try:
        order = ctx.checkout.create_checkout(product.id, variant.id, qty, buyer, channel="web", referrer=referrer or "direct")
    except (CheckoutError, KeyError) as e:
        return buy_form(ctx, seller_id, variant_id, form, str(e), 422)
    return RedirectResponse(f"/pay/{order.id}", status_code=303, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ the cart
# One cart per shop, in a cookie that holds nothing but "variant:quantity" pairs. Everything else (titles, prices, stock, whether the
# shop is open) is looked up again on every request, so a tampered cookie can only ever ask for things a buyer could ask for anyway.
CART_COOKIE = "cart_"
CART_MAX_LINES = 10
CART_SECONDS = 7 * 24 * 3600
_VARIANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def cart_cookie(seller_id: str) -> str:
    """One cookie per shop, so a buyer can hold a cart in two shops and the two never mix."""
    return CART_COOKIE + re.sub(r"[^A-Za-z0-9_-]", "", seller_id)[:64]


def _read_cart(ctx: "AppContext", raw: str | None) -> list[tuple[str, int]]:
    """'variant:qty,variant:qty' -> [(variant_id, qty)], whatever the cookie holds: bad pairs are skipped, a variant counts once,
    a quantity is clamped to 1..MAX_QTY_PER_ORDER and there are at most CART_MAX_LINES lines."""
    top = max(1, ctx.settings.max_qty_per_order)
    found: dict[str, int] = {}
    for part in (raw or "")[:1000].split(","):
        vid, _, count = part.partition(":")
        vid = vid.strip()
        if not _VARIANT_ID.match(vid) or vid in found:
            continue
        try:
            found[vid] = max(1, min(top, int(count)))
        except ValueError:
            continue
        if len(found) >= CART_MAX_LINES:
            break
    return list(found.items())


@dataclass
class CartLine:
    product: Product
    variant: object
    qty: int
    view: dict | None        # what a buyer sees of the variant (price, availability); None when it is sold out


def _cart_lines(ctx: "AppContext", seller_id: str, raw: str | None) -> list[CartLine]:
    """The cart's lines in the order they were added: only variants of this shop's published products, and none at all when the
    shop is not open to buyers. A variant that is gone is dropped; one that sold out stays, flagged (`view` is None)."""
    wanted = _read_cart(ctx, raw)
    if not wanted or not _open(ctx, seller_id):
        return []
    seller = ctx.store.get_seller(seller_id)
    ids = {vid for vid, _ in wanted}
    known: dict[str, tuple[Product, object, dict | None]] = {}
    for p in ctx.store.list_products(seller_id=seller_id, status="published"):
        hit = [v for v in p.variants if v.id in ids]
        if hit:
            shown = {x["variant_id"]: x for x in ctx.catalog.agent_view(p, seller)["variants"]}
            for v in hit:
                known[v.id] = (p, v, shown.get(v.id))
    return [CartLine(known[vid][0], known[vid][1], qty, known[vid][2]) for vid, qty in wanted if vid in known]


def _with_cart(ctx: "AppContext", response, seller_id: str, lines: list[tuple[str, int]]):
    name = cart_cookie(seller_id)
    if lines:
        response.set_cookie(name, ",".join(f"{v}:{q}" for v, q in lines), max_age=CART_SECONDS, httponly=True, samesite="lax",
                            secure=ctx.settings.public_base_url.startswith("https"), path="/")
    else:
        response.delete_cookie(name, path="/")
    return response


def _cart_link(ctx: "AppContext", seller_id: str, cart_raw: str | None) -> str:
    """The cart button with the number of items in it."""
    count = sum(ln.qty for ln in _cart_lines(ctx, seller_id, cart_raw) if ln.view is not None)
    badge = f"<span class=cartn aria-hidden=true>{count}</span>" if count else ""
    label = f"Cart, {count} item{'' if count == 1 else 's'}" if count else "Cart"
    return f'<a class="iconbtn cartlink" href="/stores/{escape(seller_id)}/cart" aria-label="{label}">{pages.ico("bag")}{badge}</a>'


def _bar_action(ctx: "AppContext", seller_id: str, cart_raw: str | None) -> str:
    """The top bar's right side: the cart and the share button."""
    return f"<span class=barend>{_cart_link(ctx, seller_id, cart_raw)}{SHARE}</span>"


def cart_badge(ctx: "AppContext", seller_id: str, cart_raw: str | None) -> HTMLResponse:
    """Just the cart button, for the page script: a page it brings back from the history must show today's count, not the one it had."""
    if not _open(ctx, seller_id[:64]):
        return HTMLResponse("", status_code=404, headers={"Cache-Control": "no-store"})
    return HTMLResponse(_cart_link(ctx, seller_id[:64], cart_raw), headers={"Cache-Control": "no-store"})


def _to(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


def _gone() -> HTMLResponse:
    return _not_found("This item was not found. It may have been sold out or removed.")


def cart_change(ctx: "AppContext", seller_id: str, action: str, cart_raw: str | None, raw: bytes) -> HTMLResponse | RedirectResponse:
    """add / update / remove one variant, then back to the cart (a plain redirect, so it works without JavaScript)."""
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        return _not_found("This store was not found.")
    if not seller.open_for_buyers:
        return _holding_page()
    try:
        form = parse_form(raw)
        count = int(form.get("qty", "1") or "1")
    except CheckoutError as e:
        return cart_page(ctx, seller.id, cart_raw, str(e), 413)
    except ValueError:
        return cart_page(ctx, seller.id, cart_raw, "Quantity must be a number.", 422)
    top = max(1, ctx.settings.max_qty_per_order)
    vid = form.get("variant_id", "")[:64]
    current = dict(_read_cart(ctx, cart_raw))
    if action == "add":
        found = _find(ctx, seller.id, vid)
        if found is None:
            return _gone()
        product, variant = found
        if not any(x["variant_id"] == variant.id for x in ctx.catalog.agent_view(product, seller)["variants"]):
            return cart_page(ctx, seller.id, cart_raw, f"{product.title} is sold out.", 422)
        if vid not in current and len(current) >= CART_MAX_LINES:
            return cart_page(ctx, seller.id, cart_raw, f"Your cart is full ({CART_MAX_LINES} items). Remove one to add another.", 422)
        current[vid] = min(top, current.get(vid, 0) + max(1, count))
    elif action == "update":
        if vid in current:
            if count <= 0:
                del current[vid]
            else:
                current[vid] = min(top, count)
    elif action == "remove":
        current.pop(vid, None)
    else:
        return _not_found("This page was not found.")
    return _with_cart(ctx, _to(f"/stores/{seller.id}/cart"), seller.id, list(current.items()))


def _line_item(ctx: "AppContext", ln: CartLine, extra: str = "", attrs: str = "") -> str:
    p, v = ln.product, ln.variant
    label = _label(v.options)
    thumb = f'<div class=media style="{pages.tone_style(p.id)}">{_media(ctx, p, hero=True)}</div>'
    price = (f"<p class=price>{escape(rupiah(v.price))} × {ln.qty} = {escape(rupiah(v.price * ln.qty))}</p>" if ln.view is not None
             else "<p class=price>Sold out</p>")
    return (f'<div class=item{attrs}>{thumb}<div><b>{escape(p.title)}</b>' + (f"<small>{escape(label)}</small>" if label else "")
            + f"{price}{extra}</div></div>")


def cart_page(ctx: "AppContext", seller_id: str, cart_raw: str | None = "", error: str = "", status: int = 200) -> HTMLResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        return _not_found("This store was not found.")
    if not seller.open_for_buyers:
        return _holding_page()
    sid = escape(seller.id)
    back = (f"/stores/{seller.id}", seller.name)
    alert = (f'<div class=alert role=alert>{pages.ico("alert")}<div><b>That did not work</b>{escape(_sentence(error))}</div></div>' if error else "")
    lines = _cart_lines(ctx, seller.id, cart_raw)
    if not lines:
        body = (f'<div class=stack>{alert}<section class=band><h2>Your cart is empty</h2>'
                '<p class=note style="margin:.35rem 0 1rem">Add something from the store and it waits here.</p>'
                f'<a class="btn alt" href="/stores/{sid}">Browse the store</a></section></div>')
        return _respond(layout(f"Cart · {seller.name}", body, h1="Your cart", back=back), status)
    rows, total, gone, confirm = [], 0, False, False
    for i, ln in enumerate(lines):
        vid, pid = escape(ln.variant.id), escape(ln.product.id)
        if ln.view is None:
            gone = True
        else:
            total += ln.variant.price * ln.qty
            confirm = confirm or ln.view["availability"] == "confirm_with_seller"
        update = ("" if ln.view is None else
                  f'<form method=post action="/stores/{sid}/cart/update"><input type=hidden name=variant_id value="{vid}">'
                  f'<label class=sr for=qty-{i}>Quantity</label><input id=qty-{i} name=qty type=number value="{ln.qty}" min=1 '
                  f'max={ctx.settings.max_qty_per_order} inputmode=numeric required><button class="btn alt" type=submit>Update</button></form>')
        remove = (f'<form method=post action="/stores/{sid}/cart/remove"><input type=hidden name=variant_id value="{vid}">'
                  '<button class="btn quiet" type=submit>Remove</button></form>')
        change = f'<a href="/stores/{sid}/p/{pid}?v={vid}">Change option</a>'
        rows.append(f'<div class="cartline{" gone" if ln.view is None else ""}">{_line_item(ctx, ln, change)}'
                    f'<div class=ops>{update}{remove}</div></div>')
    note = (f'<div class=callout style="margin-top:1rem">{pages.ico("alert")}<span>Some items are sold out. Remove them to continue.</span></div>'
            if gone else "")
    hold = (f'<div class=callout style="margin-top:1rem">{pages.ico("clock")}<span>The seller confirms stock first. '
            'The payment code appears as soon as they do.</span></div>' if confirm and not gone else "")
    summary = ('<section class=band><h2 class=cap>Summary</h2><ul class=lines>'
               f'<li><span>Subtotal</span><span>{escape(rupiah(total))}</span></li>'
               '<li><span>Shipping</span><span>Added on the next page</span></li></ul>'
               f'<p class=fine style="margin-top:.75rem">Shipping uses the cheapest courier. <a href="/stores/{sid}">Keep shopping</a></p>{note}{hold}</section>')
    go = ('<button class=btn type=button disabled>Checkout</button>' if gone or total <= 0
          else f'<a class=btn href="/stores/{sid}/cart/checkout">Checkout</a>')
    dock = f'<div class=dock><div class=tot><small>Subtotal</small><b>{escape(rupiah(total))}</b></div>{go}</div>'
    body = f'<div class=stack>{alert}<section class=band><h2 class=cap>Items</h2>{"".join(rows)}</section>{summary}{dock}</div>'
    return _respond(layout(f"Cart · {seller.name}", body, h1="Your cart", back=back), status)


def cart_form(ctx: "AppContext", seller_id: str, cart_raw: str | None = "", values: dict | None = None, error: str = "",
              status: int = 200) -> HTMLResponse | RedirectResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        return _not_found("This store was not found.")
    if not seller.open_for_buyers:
        return _holding_page()
    sid = escape(seller.id)
    lines = _cart_lines(ctx, seller.id, cart_raw)
    if not lines or any(ln.view is None for ln in lines):       # nothing to order, or something sold out: the cart says why
        return _to(f"/stores/{seller.id}/cart")
    v = values or {}
    bad = _bad_field(error) if error else ""
    total = sum(ln.variant.price * ln.qty for ln in lines)
    rows = "".join(_line_item(ctx, ln, attrs=' style="margin-top:.9rem"' if i else "") for i, ln in enumerate(lines))
    items = (f'<section class=band><h2 class=cap>Your order</h2>{rows}'
             f'<p style="margin-top:1rem"><a href="/stores/{sid}/cart">Edit cart</a></p></section>')
    confirm = (f'<div class=callout style="margin-top:1rem">{pages.ico("clock")}<span>The seller confirms stock first. '
               'The payment code appears on the next page as soon as they do.</span></div>'
               if any(ln.view["availability"] == "confirm_with_seller" for ln in lines) else "")
    summary = ('<section class=band><h2 class=cap>Summary</h2><ul class=lines>'
               f'<li><span>Subtotal</span><span>{escape(rupiah(total))}</span></li>'
               '<li><span>Shipping</span><span>Added on the next page</span></li></ul>'
               f'<p class=fine style="margin-top:.75rem">Shipping uses the cheapest courier. Your items are held for '
               f'{ctx.settings.reservation_ttl_minutes} minutes after you place the order.</p>{confirm}</section>')
    dock = (f'<div class=dock><div class=tot><small>Subtotal</small><b>{escape(rupiah(total))}</b></div>'
            '<button class=btn type=submit data-busy="Placing your order&hellip;">Place order</button></div>')
    alert = (f'<div class=alert id=checkout-error role=alert>{pages.ico("alert")}<div><b>We could not place your order</b>{escape(_sentence(error))}</div></div>'
             if error else "")
    body = f'<form class=stack method=post><h1 class=sr>Checkout</h1>{alert}{demo.buy_notice(ctx, seller.id)}{items}{_details(v, bad)}{summary}{dock}</form>'
    return _respond(layout(f"Checkout · {seller.name}", body, back=(f"/stores/{seller.id}/cart", "Cart"), heading=False), status)


def cart_submit(ctx: "AppContext", seller_id: str, cart_raw: str | None, raw: bytes, referrer: str = "") -> HTMLResponse | RedirectResponse:
    try:
        form = parse_form(raw)
    except CheckoutError as e:
        return cart_form(ctx, seller_id, cart_raw, error=str(e), status=413)
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None or not seller.open_for_buyers:
        return cart_form(ctx, seller_id, cart_raw)      # the same answers as the form: not found, or the holding page
    lines = _cart_lines(ctx, seller.id, cart_raw)
    if not lines or any(ln.view is None for ln in lines):
        return _to(f"/stores/{seller.id}/cart")
    postal = form.get("postal", "").strip()
    if not (postal.isdigit() and len(postal) == 5):
        return cart_form(ctx, seller_id, cart_raw, form, "Postal code must be 5 digits.", 422)
    buyer = Address(name=form.get("name", "").strip(), phone=_clean_phone(form.get("phone", "")),
                    line1=" ".join(form.get("address", "").split()), city=form.get("city", "").strip(),
                    postal_code=postal, country="ID")
    items = [{"product_id": ln.product.id, "variant_id": ln.variant.id, "qty": ln.qty} for ln in lines]
    try:
        order = ctx.checkout.create_checkout(buyer=buyer, items=items, channel="web", referrer=referrer or "direct")
    except (CheckoutError, KeyError) as e:
        return cart_form(ctx, seller_id, cart_raw, form, str(e), 422)
    return _with_cart(ctx, _to(f"/pay/{order.id}"), seller.id, [])


# ------------------------------------------------------------------ the order page
def _summary(order: Order) -> str:
    lines = [(f"{i.title} × {i.qty}", rupiah(i.unit_price * i.qty)) for i in order.items]
    lines.append(("Shipping", rupiah(order.shipping_fee)))
    return pages.summary(lines, ("Total", rupiah(order.total)))


def order_page(ctx: "AppContext", order_id: str) -> HTMLResponse:
    order = ctx.store.get_order(order_id[:64])
    if order is None:
        return _not_found("This order was not found.")
    seller = ctx.store.get_seller(order.seller_id)
    live, progress, extra, msg = "", "", "", ""
    s = order.status
    if s == OrderStatus.AWAITING_SELLER_CONFIRMATION:
        progress, live = pages.track(STEPS, 1), s.value
        msg = pages.status("wait", "Waiting for the seller",
                           "<p>The seller is confirming that the item is in stock. The payment code appears here as soon as they do; "
                           "this page updates by itself.</p>")
    elif s == OrderStatus.AWAITING_PAYMENT and order.payment:
        progress, live = pages.track(STEPS, 1), f"{s.value}:{order.payment.status}"
        left = max(0, int((order.expires_at - now()).total_seconds())) if order.expires_at else None
        extra = (pages.pay_card(rupiah(order.total), order.payment.qr_string, left, pages.clock(order.expires_at) if order.expires_at else "")
                 + demo.pay_button(ctx, order))
    elif s in (OrderStatus.PAID, OrderStatus.ACCEPTED):
        progress = pages.track(STEPS, 2)
        msg = pages.status("ok", "Paid", "<p>The seller will pack and ship your order. The tracking number will appear here.</p>")
    elif s == OrderStatus.SHIPPED and order.shipment and order.shipment.status == "returning":
        progress = pages.track(STEPS, 2)
        msg = pages.status("wait", "Coming back to the seller",
                           "<p>The courier could not deliver your parcel and is taking it back to the seller. "
                           "You are refunded in full when it arrives.</p>", icon="undo")
    elif s == OrderStatus.SHIPPED and order.shipment and order.shipment.status == "failed":
        progress = pages.track(STEPS, 2)
        msg = pages.status("wait", "A problem with the delivery",
                           "<p>The courier reported a problem with this shipment. Our team has been alerted and will follow up "
                           "with you and the seller.</p>", icon="alert")
    elif s == OrderStatus.SHIPPED and order.shipment:
        progress = pages.track(STEPS, 2)
        waybill = order.shipment.waybill or ""
        copy = (f'<div class=way><button class="btn quiet sm" type=button data-copy="{escape(waybill)}">{pages.ico("copy", "sm")}Copy number</button></div>'
                if waybill else "")
        msg = pages.status("ok", "On its way",
                           f"<p>Courier: <b>{escape(order.shipment.courier.upper())}</b></p>"
                           f"<p>Your tracking number: <code>{escape(waybill or 'pending')}</code></p>{copy}", icon="truck")
    elif s == OrderStatus.DELIVERED:
        progress = pages.track(STEPS, len(STEPS))
        due = order.payout_due_at if order.payout_due_at and order.payout_due_at > now() else None
        detail = "<p>Your order arrived.</p>"
        if due and order.return_request is None:
            detail = (f"<p>If the item is damaged, wrong or not as described, you can ask for a return until {escape(pages.stamp(due))}.</p>"
                      f'<a class="btn alt" href="/pay/{escape(order.id)}/return">Ask for a return</a>')
        msg = pages.status("ok", "Delivered", detail, icon="package")
    elif s == OrderStatus.RETURN_REQUESTED:
        msg = pages.status("wait", "Return requested", "<p>Your return request is with the seller, who answers within 48 hours. "
                                                         "No answer counts as approval.</p>", icon="undo")
    elif s == OrderStatus.RETURN_APPROVED:
        msg = pages.status("ok", "Return approved", "<p>Send the item back to the seller. The refund starts when the seller confirms it "
                                                    "arrived; if nothing arrives within 7 days the return lapses.</p>", icon="undo")
    elif s == OrderStatus.RETURN_DISPUTED:
        msg = pages.status("wait", "Under review", "<p>The seller disagrees with the return, so our team is reviewing it. "
                                                    "There is nothing more to do now.</p>", icon="shield")
    elif s == OrderStatus.REFUNDED:
        done = order.payment is not None and order.payment.status == "refunded"
        lost = ("<p>The courier lost the parcel, so you are refunded in full.</p>"
                if order.shipment and order.shipment.status == "lost" and not order.return_request else "")
        msg = (pages.status("ok", "Refunded", lost + "<p>The money is on its way back to you.</p>", icon="wallet") if done else
               pages.status("wait", "Refund in progress", lost + "<p>Your refund is being sent by our team and can take a few days.</p>", icon="wallet"))
    elif s == OrderStatus.PAID_OUT:
        progress = pages.track(STEPS, len(STEPS))
        msg = pages.status("ok", "Completed", "<p>Delivered and completed. The return window has closed.</p>")
    else:   # rejected or expired
        again = f'<a class="btn alt" href="/stores/{escape(seller.id)}">Back to the store</a>' if seller else ""
        msg = pages.status("bad", "Cancelled or expired",
                           "<p>You were not charged. If you still want the item, start again from the store page.</p>" + again)
    links = ""
    if seller is not None:
        links = (f'<p class=links><a href="/stores/{escape(seller.id)}/returns">{pages.ico("shield", "sm")}Return policy</a>'
                 f'<a href="/policy/payouts">{pages.ico("wallet", "sm")}How payouts are held</a>'
                 f'<a href="/stores/{escape(seller.id)}">{pages.ico("bag", "sm")}{escape(seller.name)}</a></p>')
    body = f'<div class=stack><h1 class=sr>Your order{escape(" from " + seller.name) if seller else ""}</h1>{progress}{msg}{extra}{_summary(order)}</div>{links}'
    return _respond(layout("Your order", body, live=live, heading=False,
                           back=(f"/stores/{seller.id}", seller.name) if seller else None))


# ------------------------------------------------------------------ returns for people
def return_form(ctx: "AppContext", order_id: str, values: dict | None = None, error: str = "", status: int = 200) -> HTMLResponse:
    order = ctx.store.get_order(order_id[:64])
    if order is None:
        return _not_found("This order was not found.")
    v = values or {}
    seller = ctx.store.get_seller(order.seller_id)
    reasons = [("damaged", "The item is damaged"), ("wrong_item", "I received the wrong item"),
               ("not_as_described", "It is not as described")]
    offers_mind = (order.return_terms.accepts_change_of_mind if order.return_terms
                   else bool(seller and seller.return_policy.accepts_change_of_mind))
    if offers_mind:
        reasons.append(("changed_mind", "I changed my mind (product price only, I pay the return shipping)"))
    options = "".join(f'<option value="{k}"{" selected" if v.get("reason") == k else ""}>{escape(t)}</option>' for k, t in reasons)
    alert = (f'<div class=alert role=alert>{pages.ico("alert")}<div><b>We could not send the request</b>{escape(_sentence(error))}</div></div>'
             if error else "")
    form = ('<form method=post><section class=band>'
            '<div class=field><label for=reason>Why do you want to return it?</label>'
            f'<select id=reason name=reason>{options}</select></div>'
            '<div class=field><label for=phone>The phone number you used when ordering</label><span class=hint id=phone-hint>This confirms the order is yours.</span>'
            f'<input id=phone name=phone type=tel autocomplete=tel inputmode=tel aria-describedby=phone-hint '
            f'value="{escape(v.get("phone", ""))}" required maxlength=20></div>'
            '<div class=field><label for=note>What is wrong? (optional)</label>'
            f'<textarea id=note name=note rows=3 maxlength={FIELD_LIMITS["note"]}>{escape(v.get("note", ""))}</textarea></div>'
            '<p class=fine>The seller answers within 48 hours. You send the item back and pay that shipping; '
            'the refund starts when the seller confirms it arrived.</p></section>'
            '<button class="btn block" style="margin-top:1rem" type=submit data-busy="Sending&hellip;">Send return request</button></form>')
    return _respond(layout("Ask for a return", f'<div class=stack>{alert}{_summary(order)}{form}</div>',
                           back=(f"/pay/{order.id}", "Your order"), h1="Ask for a return"), status)


def return_submit(ctx: "AppContext", order_id: str, raw: bytes) -> HTMLResponse | RedirectResponse:
    try:
        form = parse_form(raw)
    except CheckoutError as e:
        return return_form(ctx, order_id, error=str(e), status=413)
    try:
        ctx.returns.request(order_id[:64], form.get("phone", ""), form.get("reason", ""), form.get("note", ""), [])
    except (CheckoutError, KeyError) as e:
        return return_form(ctx, order_id, form, str(e), 422)
    return RedirectResponse(f"/pay/{order_id[:64]}", status_code=303, headers={"Cache-Control": "no-store"})
