"""The public store link: one page per seller that people and AI agents can both use.

    GET  /stores/{seller_id}                     the catalog (schema.org JSON-LD inside, pointers to MCP and UCP)
    GET  /stores/{seller_id}/p/{product_id}      one product: photos, options, seller, return protection, a "Buy now" bar
    GET  /stores/{seller_id}/buy/{variant_id}    checkout: quantity, contact and delivery details, payment method
    POST /stores/{seller_id}/buy/{variant_id}    creates the same checkout an agent would, then goes to the pay page
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


def _find(ctx: "AppContext", seller_id: str, variant_id: str) -> tuple[Product, object] | None:
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


def store_page(ctx: "AppContext", seller_id: str) -> HTMLResponse:
    seller = ctx.store.get_seller(seller_id[:64])
    if seller is None:
        return _not_found("This store was not found.")
    base = ctx.settings.public_base_url.rstrip("/")
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
    return _respond(layout(seller.name, head_block + grid + agents, head=head, index=True, wide=True, heading=False, flush=True, action=SHARE))


# ------------------------------------------------------------------ one product
def _product_of(ctx: "AppContext", seller_id: str, product_id: str) -> Product | None:
    return next((p for p in ctx.store.list_products(seller_id=seller_id, status="published") if p.id == product_id), None)


def product_page(ctx: "AppContext", seller_id: str, product_id: str, variant_id: str = "") -> HTMLResponse:
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
        buy = f'<a class=btn data-go href="/stores/{sid}/buy/{escape(current["variant_id"])}">Buy now</a>'
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
                           flush=True, action=SHARE, head=head, index=True))


# ------------------------------------------------------------------ checkout
_FIELD_WORDS = (("postal", "postal"), ("phone", "phone"), ("name", "name"), ("address", "address"), ("city", "city"), ("quantity", "qty"))


def _bad_field(error: str) -> str:
    low = error.lower()
    return next((field for word, field in _FIELD_WORDS if word in low), "")


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

    def field(name, title, kind="text", *, hint="", auto="", extra="", default=""):
        hint_id = f"{name}-hint"
        flag = " aria-invalid=true aria-describedby=checkout-error" if name == bad else (f" aria-describedby={hint_id}" if hint else "")
        return (f'<div class="field{" bad" if name == bad else ""}"><label for={name}>{title}</label>' + (f'<span class=hint id={hint_id}>{hint}</span>' if hint else "")
                + f'<input id={name} name={name} type={kind} value="{escape(v.get(name, default))}" maxlength={FIELD_LIMITS.get(name, 3)} '
                  f'autocomplete={auto or "off"}{flag} required {extra}></div>')
    label = _label(variant.options)
    thumb = f'<div class=media style="{pages.tone_style(product.id)}">{_media(ctx, product, hero=True)}</div>'
    qty = (f'<div class=qtyrow><label for=qty>Quantity</label><div class=stepper>'
           f'<button class=st type=button data-step=-1 aria-label="One less">{pages.ico("minus")}</button>'
           f'<input id=qty name=qty type=number value="{shown_qty}" maxlength=3 autocomplete=off required min=1 max={ctx.settings.max_qty_per_order} inputmode=numeric>'
           f'<button class=st type=button data-step=1 aria-label="One more">{pages.ico("plus")}</button></div></div>')
    item = (f'<section class=band><h2 class=cap>Your order</h2><div class=item>{thumb}<div><b>{escape(product.title)}</b>'
            + (f'<small>{escape(label)}</small>' if label else "") + f'<p class=price>{escape(rupiah(variant.price))}</p>'
            f'<a href="/stores/{sid}/p/{escape(product.id)}?v={escape(variant.id)}">Change option</a></div></div>{qty}</section>')
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
            f'{item}{contact}{delivery}{payment}{summary}{dock}</form>')
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
