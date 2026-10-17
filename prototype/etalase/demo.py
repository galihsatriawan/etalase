"""The judge sandbox (DEMO_MODE): a shared demo seller, the things a reviewer needs to run the whole story without any account,
and the simulation steps that stand in for the outside world (the buyer's bank app, the courier, the passing of time).

Everything here is behind DEMO_MODE, which security.validate_settings refuses unless payment, shipping and messaging are all
simulated, so nothing in this file can touch real money, a real courier or a real WhatsApp number. The seller is a single
shared account: whatever one visitor does, the next visitor sees. See JUDGES.md.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from .catalog import readiness
from .catalog.normalize import make_sku
from .models import Address, CheckoutError, Order, OrderStatus, Product, ProductStatus, Seller, Variant, now

if TYPE_CHECKING:
    from .container import AppContext

log = logging.getLogger("etalase.demo")

DEMO_SELLER_ID = "sel_demo"
DEMO_NUMBER = "6280000000001"        # never a real number: DEMO_MODE only runs with the console messenger
DEMO_NAME = "Toko Demo Rina"
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"
SAMPLE_PHOTOS = ("voal-hijab.png", "pashmina-silk.png", "kemeja-linen.png")    # the only photos a visitor can send
VISITOR_PRODUCT_MINUTES = 60         # a product a visitor publishes disappears from the shop after this long
MAX_OUTBOX = 1500                    # the console outbox is trimmed to this many messages
ACTIONS = {"pay": "Pay with QRIS (simulated)", "deliver": "Courier delivers (simulated)", "release": "Skip the return window"}
# What a visitor playing the seller does next, by order status: the console says it so nobody has to guess.
NEXT = {
    "awaiting_seller_confirmation": "Confirm the stock in the chat: the buyer is waiting.",
    "awaiting_payment": "Waiting for the buyer to pay.",
    "paid": "Tap Accept in the chat.",
    "accepted": "Send a tracking number in the chat, for example \u201ctracking JNE123456\u201d.",
    "shipped": "The courier has the parcel.",
    "delivered": "The buyer can ask for a return until the window ends; skip it to release the payout.",
    "return_requested": "The buyer asked for a return: answer in the chat.",
    "return_approved": "Waiting for the item to come back: tap Item received in the chat.",
    "return_disputed": "The seller and buyer disagree: an operator would decide.",
    "paid_out": "Done: the payout was released to the seller.",
    "refunded": "Done: the buyer was refunded.",
    "rejected": "The seller turned the order down.",
    "expired": "Nobody paid in time, so the stock went back on sale.",
}


@dataclass(frozen=True)
class SeedVariant:
    key: str
    options: dict
    price: int
    stock: int
    floor: int = 10        # topped up to `stock` whenever it falls below this


@dataclass(frozen=True)
class SeedProduct:
    id: str
    title: str
    category: str
    description: str
    photo: str
    weight_gram: int
    variants: tuple[SeedVariant, ...] = field(default_factory=tuple)


SEED = (
    SeedProduct(
        "prd_demo_voal", "Voal Hijab", "Hijab",
        "Lightweight voal square hijab, 110 x 110 cm, soft and easy to shape. Machine washable.",
        "voal-hijab.png", 120,
        (SeedVariant("voal_black", {"color": "Black"}, 85_000, 60), SeedVariant("voal_navy", {"color": "Navy"}, 85_000, 60),
         SeedVariant("voal_mocca", {"color": "Mocca"}, 85_000, 60))),
    SeedProduct(
        "prd_demo_pashmina", "Silk Pashmina", "Hijab",
        "Soft silk-blend pashmina, 70 x 180 cm. The cream colour is nearly sold out, so the seller is asked to confirm it first.",
        "pashmina-silk.png", 150,
        (SeedVariant("pashmina_pink", {"color": "Pink"}, 145_000, 40), SeedVariant("pashmina_sage", {"color": "Sage"}, 145_000, 40),
         SeedVariant("pashmina_cream", {"color": "Cream"}, 145_000, 2, floor=1))),
    SeedProduct(
        "prd_demo_linen", "Linen Shirt", "Fashion",
        "Relaxed linen shirt with a straight cut and a front button placket. Breathable and light for hot days.",
        "kemeja-linen.png", 350,
        (SeedVariant("linen_m", {"size": "M"}, 210_000, 40), SeedVariant("linen_l", {"size": "L"}, 210_000, 40),
         SeedVariant("linen_xl", {"size": "XL"}, 210_000, 40))),
)
SEED_PRODUCT_IDS = {p.id for p in SEED}


def _product(spec: SeedProduct) -> Product:
    variants = []
    for v in spec.variants:
        variant = Variant(id=f"var_demo_{v.key}", sku=make_sku(spec.title, v.options), options=dict(v.options), price=v.price,
                          weight_gram=spec.weight_gram)
        variant.stock.on_hand = v.stock
        variant.stock.last_confirmed_at = now()
        variant.stock.source = "demo"
        variants.append(variant)
    product = Product(id=spec.id, seller_id=DEMO_SELLER_ID, title=spec.title, description=spec.description,
                      category=spec.category, images=[f"samples/{spec.photo}"], variants=variants)
    product.readiness_score, product.readiness_missing = readiness.score(product)
    product.status = ProductStatus.PUBLISHED
    return product


DEMO_SLUG = "demo"


def _give_address(ctx: "AppContext") -> None:
    """With STORE_BASE_DOMAIN set, the demo shop answers on its own address (demo.<domain>), so a reviewer can see what a shop
    address is. When "demo" is not allowed here (it is the platform host's own label, say), the address comes from the shop name
    instead. Never a reason to stop the sandbox from starting."""
    from .seller import slugs
    seller = ctx.store.get_seller(DEMO_SELLER_ID)
    if not slugs.subdomains_enabled(ctx.settings) or seller is None or seller.slug:
        return
    try:
        slugs.claim(ctx, DEMO_SELLER_ID, DEMO_SLUG)
    except slugs.SlugError:
        slugs.assign(ctx, DEMO_SELLER_ID)


def seed(ctx: "AppContext") -> None:
    """Create the demo seller and its products if they are not there yet (safe to run again). Fixed ids keep the shop address
    the same across restarts, so a link given to a reviewer keeps working."""
    if ctx.store.get_seller(DEMO_SELLER_ID) is None:
        ctx.store.put_seller(Seller(
            id=DEMO_SELLER_ID, name=DEMO_NAME, whatsapp=DEMO_NUMBER, kyc_status="verified", payout_account_id="mock-sub-account",
            origin=Address(name=DEMO_NAME, line1="Jl. Contoh No. 1", city="Jakarta", postal_code="12950")))
    _give_address(ctx)
    for spec in SEED:
        if ctx.store.get_product(spec.id) is None:
            ctx.store.put_product(_product(spec))
    keep_fresh(ctx)
    log.info("Demo sandbox ready: seller %s, %d products", DEMO_SELLER_ID, len(SEED))


def keep_fresh(ctx: "AppContext") -> None:
    """Run from the background loop. A sandbox that sells out, goes stale or fills up with strangers' products stops being a
    demo, so: seeded stock is topped up and re-confirmed (a stale stock count would switch the product to confirm-first),
    products a visitor published expire after an hour, and the console outbox stays small."""
    for spec in SEED:
        product = ctx.store.get_product(spec.id)
        if product is None:
            continue
        by_id = {v.id: v for v in product.variants}
        for sv in spec.variants:
            v = by_id.get(f"var_demo_{sv.key}")
            if v is None:
                continue
            on_hand = v.stock.on_hand or 0
            confirmed = v.stock.last_confirmed_at
            stale = confirmed is None or now() - confirmed > timedelta(hours=12)
            short = (v.stock.available or 0) < sv.floor      # what a buyer can take: on hand minus what unpaid checkouts hold
            if short or stale:
                # Held stock comes back when a checkout expires, but a visitor holding every unit should not empty the shop meanwhile.
                ctx.store.set_stock(DEMO_SELLER_ID, spec.id, v.id, sv.stock + v.stock.reserved + v.stock.buffer if short else on_hand, "demo")
    cutoff = now() - timedelta(minutes=VISITOR_PRODUCT_MINUTES)
    for p in ctx.store.list_products(seller_id=DEMO_SELLER_ID, status=ProductStatus.PUBLISHED):
        if p.id not in SEED_PRODUCT_IDS and p.updated_at < cutoff:
            p.status = ProductStatus.DRAFT
            ctx.store.put_product(p)
    outbox = getattr(ctx.messenger, "outbox", None)
    if outbox is not None and len(outbox) > MAX_OUTBOX:
        del outbox[:len(outbox) - MAX_OUTBOX]


# ------------------------------------------------------------------ the steps the outside world would do
class DemoError(Exception):
    """A simulation step that does not apply to the order as it is now. The text is safe to show."""


def is_demo_order(ctx: "AppContext", order: Order | None) -> bool:
    return bool(order) and ctx.settings.demo_mode and order.seller_id == DEMO_SELLER_ID


def actions_for(order: Order) -> list[str]:
    if order.status == OrderStatus.AWAITING_PAYMENT and order.payment:
        return ["pay"]
    if order.status == OrderStatus.SHIPPED and order.shipment and order.shipment.status in ("booked", "picked_up", "in_transit"):
        return ["deliver"]
    if order.status == OrderStatus.DELIVERED:
        return ["release"]
    return []


def simulate(ctx: "AppContext", order_id: str, action: str) -> Order:
    """Do what the buyer's bank app, the courier or the calendar would do. Only for orders of the demo seller."""
    order = ctx.store.get_order(order_id[:64])
    if not is_demo_order(ctx, order):
        raise KeyError(order_id)
    if action not in actions_for(order):
        raise DemoError("That step does not apply to this order right now.")
    try:
        if action == "pay":
            return ctx.checkout.payment_succeeded(order.payment.reference)
        if action == "deliver":
            return ctx.checkout.shipment_update(order.id, "delivered")

        def due(o: Order) -> bool:       # "release": the return window is over, the payout is due now
            o.payout_due_at = now()
            return True
        ctx.store.update_order(order.id, due)
        ctx.process_due()
        return ctx.store.get_order(order.id)
    except CheckoutError as e:
        raise DemoError(str(e))


# ------------------------------------------------------------------ what the pages show
def pay_button(ctx: "AppContext", order: Order) -> str:
    """The order page's stand-in for scanning the QR code with a banking app."""
    if not is_demo_order(ctx, order) or "pay" not in actions_for(order):
        return ""
    return ('<section class=band><h2 class=cap>Demo sandbox</h2>'
            '<p class=fine>No bank is involved here. This button does what a confirmed QRIS payment would do: the order becomes paid '
            'and the seller is told in the chat.</p>'
            # The page script sends a form as a POST only when the body is not empty, so the form carries one field.
            f'<form method=post action="/demo/pay/{escape(order.id)}"><input type=hidden name=demo value=1>'
            '<button class="btn block" style="margin-top:.75rem" type=submit data-busy="Paying&hellip;">Simulate the QRIS payment</button>'
            '</form></section>')


def buy_notice(ctx: "AppContext", seller_id: str) -> str:
    """Shown above the checkout form of the demo shop: orders placed here are visible to every visitor of the sandbox."""
    if not (ctx.settings.demo_mode and seller_id == DEMO_SELLER_ID):
        return ""
    return ('<div class=callout style="margin-bottom:1rem"><span><b>Demo sandbox.</b> Please use made-up details: orders placed here can be '
            'seen by other visitors, and no real payment or delivery happens.</span></div>')


def pay_link(ctx: "AppContext", order: Order | None, base: str = "") -> str:
    """The agent's checkout page cannot post forms, so it points to the order page where the button is."""
    if order is None or not is_demo_order(ctx, order) or "pay" not in actions_for(order):
        return ""
    return (f'<p class=links><a href="{escape(base)}/pay/{escape(order.id)}">Demo sandbox: open the order page to simulate '
            'the QRIS payment</a></p>')


def order_view(ctx: "AppContext", order: Order) -> dict:
    items = ", ".join(f"{i.title} × {i.qty}" for i in order.items)
    return {"id": order.id, "status": order.status.value.replace("_", " "), "total": order.total, "items": items,
            "city": order.buyer.city, "channel": order.channel or "", "pay_url": f"/pay/{order.id}",
            "next": NEXT.get(order.status.value, ""),
            "actions": [{"id": a, "title": ACTIONS[a]} for a in actions_for(order)]}


# What the simulated seller "typed", so the console can show both sides of the chat to every visitor. Each entry remembers the
# last message Etalase had sent when it was written (None: before any), which is where it belongs in the thread.
_sent: deque = deque(maxlen=200)


def _to_demo_seller(ctx: "AppContext") -> list:
    return [m for m in list(getattr(ctx.messenger, "outbox", [])) if m.to == DEMO_NUMBER]


def record_sent(ctx: "AppContext", text: str = "", photo: str = "", tapped: bool = False) -> None:
    """Call before handing the message to the inbox, so the replies it causes land after it in the thread."""
    mine = _to_demo_seller(ctx)
    _sent.append((mine[-1] if mine else None, {"who": "me", "text": text, "photo": photo, "tapped": tapped}))


def _thread(ctx: "AppContext") -> list[dict]:
    mine, sent = _to_demo_seller(ctx), list(_sent)
    thread = [item for anchor, item in sent if anchor is None]
    for m in mine:
        thread.append({"who": "etalase", "text": m.text, "buttons": [{"id": b, "title": t} for b, t in m.buttons]})
        thread.extend(item for anchor, item in sent if anchor is m)
    return thread[-60:]


def console_state(ctx: "AppContext") -> dict:
    """Everything the simulated seller phone shows: the chat, the shop's orders and the links to try."""
    chat = _thread(ctx)
    orders = sorted(ctx.store.list_orders(DEMO_SELLER_ID), key=lambda o: o.created_at, reverse=True)[:8]
    base = ctx.settings.public_base_url.rstrip("/")
    seller = ctx.store.get_seller(DEMO_SELLER_ID)
    from .seller import slugs
    return {
        "chat": chat,
        "orders": [order_view(ctx, o) for o in orders],
        "photos": list(SAMPLE_PHOTOS),
        "number": DEMO_NUMBER,
        "ai": ctx.modes.get("ai", ""),
        "links": {"store": slugs.store_url(ctx.settings, seller) if seller else f"{base}/stores/{DEMO_SELLER_ID}",
                  "mcp": f"{base}/mcp/", "ucp": f"{base}/.well-known/ucp", "dashboard": f"{base}/"},
    }
