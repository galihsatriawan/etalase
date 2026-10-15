"""Which channel Etalase uses to reach a seller, and how a Telegram chat gets linked to a shop.

WhatsApp stays the identity: the seller signs up and signs in with the WhatsApp number. A seller can additionally link one
Telegram chat and choose it as the channel for orders, stock checks and payouts. Linking works like this:

1. In the dashboard the signed-in seller asks for a link code (`start_link`). It is signed, valid for 10 minutes, and
   carried in the Telegram deep link  t.me/<bot>?start=<code>.
2. Telegram delivers  /start <code>  to the webhook from a chat nobody has linked yet; `link_telegram` verifies the code
   and stores the chat id on the seller. Only a code obtained inside a signed-in dashboard session can link a chat, and a
   code works once: a copy of it (a forwarded link, a screenshot) is worth nothing after the seller used it. Replacing a
   chat that was already linked is announced to the old chat and to the WhatsApp number.
3. From then on the seller can write to the bot like to the WhatsApp number, and `set_channel` decides where Etalase's own
   messages go. Replies to something the seller wrote always go back through the channel it came from.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..adapters.messaging import TELEGRAM_PREFIX, ConsoleMessenger
from ..adapters.telegram import TelegramError

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.channels")
CHANNELS = ("whatsapp", "telegram")


def telegram_available(ctx: "AppContext") -> bool:
    """A real bot, or the console outbox (local demo and tests, where the bot is simulated)."""
    return ctx.telegram is not None or isinstance(getattr(ctx.messenger, "default", ctx.messenger), ConsoleMessenger)


def start_link(ctx: "AppContext", seller_id: str) -> dict:
    if not telegram_available(ctx):
        raise ValueError("Telegram is not set up on this server")
    code = ctx.auth.link_code(seller_id)
    url = None
    if ctx.telegram is not None:
        try:
            url = f"https://t.me/{ctx.telegram.bot_username()}?start={code}"
        except TelegramError:
            log.warning("could not look up the Telegram bot name", exc_info=True)
    return {"code": code, "command": f"/start {code}", "url": url, "expires_in": 600}


def link_telegram(ctx: "AppContext", sender: str, code: str) -> bool:
    """`sender` is "tg:<chat id>" from an update nobody has linked yet. Answers the chat either way."""
    seller_id = ctx.auth.use_link_code(code)
    seller = ctx.store.get_seller(seller_id) if seller_id else None
    if seller is None:
        ctx.messenger.send_text(sender, ctx.t("telegram_link_invalid"))
        return False
    chat = sender[len(TELEGRAM_PREFIX):]
    previous = seller.telegram_chat_id
    seller.telegram_chat_id = chat
    seller.channel = "telegram"
    ctx.store.put_seller(seller)
    ctx.messenger.send_text(sender, ctx.t("telegram_linked", shop=seller.name))
    if previous and previous != chat:    # somebody else's chat must never take over a shop silently
        for old in (TELEGRAM_PREFIX + previous, seller.whatsapp):
            try:
                ctx.messenger.send_text(old, ctx.t("telegram_replaced", shop=seller.name))
            except Exception:
                log.info("could not tell %s that the Telegram chat changed", old[:6], exc_info=True)
    return True


def set_channel(ctx: "AppContext", seller_id: str, channel: str):
    if channel not in CHANNELS:
        raise ValueError("channel must be whatsapp or telegram")
    seller = ctx.store.get_seller(seller_id)
    if channel == "telegram" and not seller.telegram_chat_id:
        raise ValueError("Connect Telegram first")
    seller.channel = channel
    ctx.store.put_seller(seller)
    try:   # confirm where messages arrive from now on; a WhatsApp window that is closed must not fail the switch
        ctx.messenger.send_text(seller.address, ctx.t("channel_switched"))
    except Exception:
        log.info("could not confirm the channel switch to seller %s", seller_id, exc_info=True)
    return seller
