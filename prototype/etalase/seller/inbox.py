"""Everything a seller sends to Etalase's WhatsApp number lands here.

- Button replies are handled deterministically (never by the model).
- Photos, voice notes and files are saved and grouped with the next text message.
- Free text goes to the ADK seller agent when Gemini is configured, otherwise to the
  offline intent parser, so the same flows work with and without credentials.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING

from ..adapters.messaging import Inbound
from ..catalog import intents
from ..models import CheckoutError, OrderStatus, Product
from .format import rupiah

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.inbox")

MEDIA_WINDOW_SECONDS = 15 * 60  # photos sent within 15 minutes belong to the next description
MAX_PENDING_MEDIA = 20           # per seller; bounds Gemini cost and memory
SEEN_MESSAGE_IDS = 5000          # WhatsApp retries webhooks, so the same message can arrive twice


class SellerInbox:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self.pending_media: dict[str, list[tuple[float, str, str]]] = defaultdict(list)  # seller -> (ts, kind, path)
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._seen:
            return True
        self._seen[message_id] = None
        if len(self._seen) > SEEN_MESSAGE_IDS:
            self._seen.popitem(last=False)
        return False

    # ------------------------------------------------------------------ entry
    async def handle(self, msg: Inbound) -> None:
        ctx = self.ctx
        if self._duplicate(msg.message_id):
            return
        seller = ctx.store.find_seller_by_whatsapp(msg.sender)
        if seller is None:
            ctx.messenger.send_text(msg.sender, ctx.t("unregistered", url=ctx.settings.public_base_url + "/"))
            return
        if msg.kind == "button":
            self.handle_button(seller.id, msg.button_id)
            return
        if msg.kind in ("image", "audio", "document"):
            if len(self.pending_media[seller.id]) >= MAX_PENDING_MEDIA:
                ctx.messenger.send_text(msg.sender, ctx.t("cannot_process", error=f"max {MAX_PENDING_MEDIA} files at once"))
                return
            try:
                path = ctx.messenger.download_media(msg.media_id, ctx.media_dir)
            except PermissionError as e:
                ctx.messenger.send_text(msg.sender, ctx.t("cannot_process", error=e))
                return
            self.pending_media[seller.id].append((time.time(), msg.kind, path))
            if msg.text:  # caption describes the product
                self.create_catalog(seller.id, [msg.text])
            elif msg.kind == "document" and path.lower().endswith(".csv"):
                with open(path, encoding="utf-8") as f:
                    self.create_catalog(seller.id, [f.read()])
            else:
                n = len(self.pending_media[seller.id])
                ctx.messenger.send_text(msg.sender, ctx.t("files_received", n=n))
            return
        await self.handle_text(seller.id, msg.text)

    async def handle_text(self, seller_id: str, text: str) -> None:
        if self.ctx.seller_agent is not None:
            reply = await self.ctx.seller_agent.respond(seller_id, text)
            seller = self.ctx.store.get_seller(seller_id)
            if reply:
                self.ctx.messenger.send_text(seller.whatsapp, reply)
            return
        self.handle_intent(seller_id, text)

    # ------------------------------------------------------------------ media
    def take_media(self, seller_id: str) -> tuple[list[str], list[str]]:
        cutoff = time.time() - MEDIA_WINDOW_SECONDS
        items = [m for m in self.pending_media.pop(seller_id, []) if m[0] >= cutoff]
        images = [p for _, kind, p in items if kind == "image"]
        audio = [p for _, kind, p in items if kind == "audio"]
        return images, audio

    # ------------------------------------------------------------------ actions
    def create_catalog(self, seller_id: str, texts: list[str]) -> list[Product]:
        ctx = self.ctx
        seller = ctx.store.get_seller(seller_id)
        images, audio = self.take_media(seller_id)
        drafts, questions = ctx.catalog.ingest(seller_id, texts, images, audio)
        if not drafts:
            ctx.messenger.send_text(seller.whatsapp, ctx.t("catalog_not_understood"))
            return []
        ctx.messenger.send_buttons(seller.whatsapp, summarize(ctx.t, drafts, questions),
                                   [("publish", ctx.t("btn_publish")), ("edit", ctx.t("btn_edit"))])
        return drafts

    def import_instagram(self, seller_id: str, source) -> list[Product]:
        ctx = self.ctx
        seller = ctx.store.get_seller(seller_id)
        drafts, skipped = ctx.catalog.import_instagram(seller_id, source, ctx.media_dir)
        if not drafts:
            ctx.messenger.send_text(seller.whatsapp, ctx.t("ig_nothing_new"))
            return []
        text = ctx.t("ig_imported", skipped=skipped) + "\n" + summarize(ctx.t, drafts, [])
        ctx.messenger.send_buttons(seller.whatsapp, text, [("publish", ctx.t("btn_publish")), ("edit", ctx.t("btn_edit"))])
        return drafts

    def handle_button(self, seller_id: str, button_id: str) -> None:
        ctx = self.ctx
        seller = ctx.store.get_seller(seller_id)
        action, _, order_id = button_id.partition(":")
        try:
            if action == "publish":
                published = ctx.catalog.publish(seller_id)
                total = len(ctx.store.list_products(seller_id=seller_id, status="published"))
                ctx.messenger.send_text(seller.whatsapp, ctx.t("published", n=len(published), total=total))
            elif action == "edit":
                ctx.messenger.send_text(seller.whatsapp, ctx.t("edit_hint"))
            elif action == "confirm":
                order = ctx.checkout.seller_confirms_stock(seller_id, order_id, available=True)
                if order.status == OrderStatus.AWAITING_PAYMENT and order.seller_confirmed:
                    ctx.messenger.send_text(seller.whatsapp, ctx.t("confirmed"))
                else:  # expired or already answered: nothing was sent to the buyer
                    ctx.messenger.send_text(seller.whatsapp, ctx.t("order_closed", status=order.status.value))
            elif action == "oos":
                order = ctx.store.get_order(order_id)
                if order and order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION:
                    ctx.checkout.seller_confirms_stock(seller_id, order_id, available=False)
                else:
                    ctx.checkout.seller_rejects_paid(seller_id, order_id)
            elif action == "accept":
                ctx.checkout.seller_accepts(seller_id, order_id)
        except CheckoutError as e:
            ctx.messenger.send_text(seller.whatsapp, ctx.t("cannot_process", error=e))

    def handle_intent(self, seller_id: str, text: str) -> None:
        ctx = self.ctx
        seller = ctx.store.get_seller(seller_id)
        it = intents.parse(text)
        try:
            if it.kind == "catalog":
                self.create_catalog(seller_id, [text])
            elif it.kind in ("set_stock", "add_stock"):
                matches = ctx.catalog.find_variants(seller_id, it.color, it.hint)
                titles = sorted({p.title for p, _ in matches})
                if not it.hint and len(titles) > 1:  # never change several products from one vague message
                    ctx.messenger.send_text(seller.whatsapp, ctx.t("which_product", color=it.color,
                                                                   titles=", ".join(titles)))
                    return
                changes = ctx.catalog.update_stock(seller_id, it.color, it.qty, hint=it.hint,
                                                   mode="set" if it.kind == "set_stock" else "add")
                if changes:
                    after = ", ".join(str(c.after) for c in changes)
                    ctx.messenger.send_text(seller.whatsapp, ctx.t("stock_updated", color=it.color, after=after))
                else:
                    ctx.messenger.send_text(seller.whatsapp, ctx.t("variant_not_found", color=it.color))
            elif it.kind == "set_price":
                changed = ctx.catalog.update_price(seller_id, it.hint, it.price) if it.price else []
                msg = (ctx.t("price_updated", titles=", ".join(p.title for p in changed), price=rupiah(it.price))
                       if changed else ctx.t("price_product_missing"))
                ctx.messenger.send_text(seller.whatsapp, msg)
            elif it.kind == "tracking":
                order = ctx.checkout.mark_shipped(seller_id, None, it.waybill)
                rate = seller.commission_rate if seller.commission_rate is not None else ctx.settings.commission_rate
                amount = order.subtotal - round(order.subtotal * rate)
                ctx.messenger.send_text(seller.whatsapp, ctx.t("tracking_saved", waybill=it.waybill, amount=rupiah(amount)))
            elif it.kind == "sales_summary":
                ctx.messenger.send_text(seller.whatsapp, sales_summary(ctx, seller_id))
            else:
                ctx.messenger.send_text(seller.whatsapp, ctx.t("help"))
        except (CheckoutError, PermissionError) as e:
            ctx.messenger.send_text(seller.whatsapp, ctx.t("cannot_process", error=e))


def summarize(t, drafts: list[Product], questions: list[str]) -> str:
    lines = [t("summary_head", n=len(drafts))]
    for p in drafts:
        variants = ", ".join(f"{v.label} ({v.stock.on_hand if v.stock.on_hand is not None else '?'})" for v in p.variants)
        price = rupiah(p.variants[0].price) if p.variants and p.variants[0].price else t("summary_no_price")
        weight = f"±{p.variants[0].weight_gram} g" if p.variants and p.variants[0].weight_gram else ""
        lines.append(f"• {p.title}: {variants} · {price} {weight}".rstrip())
        lines.append(t("summary_readiness", score=p.readiness_score)
                     + (t("summary_missing", missing=", ".join(p.readiness_missing)) if p.readiness_missing else ""))
    lines += [f"❓ {q}" for q in questions]
    lines.append(t("summary_question"))
    return "\n".join(lines)


def sales_summary(ctx: "AppContext", seller_id: str) -> str:
    done = {OrderStatus.PAID, OrderStatus.ACCEPTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.PAID_OUT}
    orders = [o for o in ctx.store.list_orders(seller_id) if o.status in done]
    gross = sum(o.subtotal for o in orders)
    paid_out = sum(o.payout.seller_amount for o in orders if o.payout)
    return ctx.t("sales", n=len(orders), gross=rupiah(gross), paid=rupiah(paid_out))
