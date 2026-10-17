"""Wires settings, store, adapters and services together (one place to swap implementations)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .adapters.messaging import ChannelRouter, ConsoleMessenger, Messenger, WhatsAppCloudMessenger
from .adapters.telegram import TelegramMessenger
from .adapters.payment import MockPayment, PaymentProvider, XenditPayment
from .adapters.shipping import BiteshipShipping, MockShipping, ShippingProvider
from .catalog.extractor import FallbackExtractor, GeminiExtractor, HeuristicExtractor
from .catalog.service import CatalogService
from .checkout.returns import ReturnService
from .checkout.service import CheckoutService
from .config import Settings, export_google_env, get_settings
from .events import EventBus
from .store import Store, build_store

log = logging.getLogger("etalase")


@dataclass
class AppContext:
    settings: Settings
    store: Store
    events: EventBus
    messenger: Messenger
    payment: PaymentProvider
    shipping: ShippingProvider
    catalog: CatalogService
    checkout: CheckoutService
    media_dir: str
    gateway: object = None
    inbox: object = None
    signup: object = None
    connector: object = None
    returns: object = None
    ucp_orders: object = None
    auth: object = None
    seller_agent: object = None
    telegram: TelegramMessenger | None = None
    t: object = None
    modes: dict = field(default_factory=dict)

    def process_due(self, when=None) -> dict:
        """The background job: expire unpaid checkouts, catch up on missed courier events, run the return timers, then pay
        out what is due. Order matters: a return that lapses this minute is paid out this minute."""
        # Each step already skips an order that raises; the guards here keep a failure in one step from stopping the others.
        empty = {"returns_auto_approved": [], "returns_lapsed": [], "returns_escalated": []}
        result: dict = {"stock_reminders": [], "expired": [], "shipments_reconciled": [], **empty, "payouts_released": [], "addresses_released": [], "ucp_webhooks": []}
        for key, step in (("stock_reminders", lambda: self.checkout.remind_stock_checks(when)),
                          ("expired", lambda: self.checkout.expire_due(when)),
                          ("shipments_reconciled", lambda: self.checkout.reconcile_shipments(when)),
                          ("timers", lambda: self.returns.sweep(when)),
                          ("payouts_released", lambda: self.checkout.release_due_payouts(when)),
                          ("addresses_released", lambda: self.signup.sweep(when)),
                          ("ucp_webhooks", lambda: self.ucp_orders.deliver_due(when))):
            try:
                out = step()
            except Exception:
                log.exception("background step %s failed", key)
                continue
            result.update(out) if key == "timers" else result.__setitem__(key, out)
        return result


def build_context(settings: Settings | None = None, echo_messages: bool = False) -> AppContext:
    from .auth import OtpAuth
    from .connector.service import ConnectorService
    from .gateway.tools import GatewayTools
    from .seller import notifications
    from .seller.i18n import Translator
    from .seller.inbox import SellerInbox
    from .seller.signup import Signup

    s = settings or get_settings()
    export_google_env(s)
    events = EventBus()
    store = build_store(s.store_backend, s.google_cloud_project)

    if s.whatsapp_token and s.whatsapp_phone_number_id:
        messenger: Messenger = WhatsAppCloudMessenger(s.whatsapp_token, s.whatsapp_phone_number_id, s.whatsapp_api_version,
                                                                template_language=s.seller_locale)
    else:
        samples = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
        messenger = ConsoleMessenger(echo=echo_messages, media_roots=[samples])
    telegram = TelegramMessenger(s.telegram_bot_token, s.telegram_bot_username) if s.telegram_bot_token else None
    if telegram:   # one Messenger for the services; the address ("tg:<chat>" or a number) picks the channel
        messenger = ChannelRouter(messenger, telegram)
    payment: PaymentProvider = XenditPayment(s.xendit_secret_key, s.xendit_callback_token) if s.xendit_secret_key else MockPayment()
    shipping: ShippingProvider = BiteshipShipping(s.biteship_api_key) if s.biteship_api_key else MockShipping()
    if s.gemini_enabled:
        extractor = FallbackExtractor(GeminiExtractor(s.gemini_model, s.google_genai_use_vertexai, s.google_cloud_project,
                                                      s.google_cloud_location, s.google_api_key), HeuristicExtractor())
    else:
        extractor = HeuristicExtractor()

    catalog = CatalogService(store, s, extractor, events)
    checkout = CheckoutService(store, s, catalog, payment, shipping, events)
    media_dir = os.getenv("ETALASE_MEDIA_DIR", "./media")
    ctx = AppContext(settings=s, store=store, events=events, messenger=messenger, payment=payment,
                     shipping=shipping, catalog=catalog, checkout=checkout, media_dir=media_dir)
    ctx.telegram = telegram
    if isinstance(messenger, ChannelRouter):   # a dead Telegram chat must not cut the seller off: fall back to their WhatsApp
        def whatsapp_for(address: str) -> str | None:
            seller = store.find_seller_by_address(address)
            return seller.whatsapp if seller else None
        messenger.fallback = whatsapp_for
    ctx.t = Translator(s.seller_locale)
    ctx.returns = ReturnService(checkout)
    ctx.gateway = GatewayTools(ctx)
    ctx.inbox = SellerInbox(ctx)
    ctx.signup = Signup(ctx)
    ctx.connector = ConnectorService(ctx)
    ctx.auth = OtpAuth(ctx, s.session_secret)
    if s.gemini_enabled:
        from .agents.seller_agent import SellerAgentRunner
        ctx.seller_agent = SellerAgentRunner(ctx)

    notifications.register(ctx)
    from .gateway import ucp_order
    ucp_order.register(ctx)
    events.subscribe("order.paid", ctx.connector.deliver_order)
    # Pilot: page the on-call operator (Pub/Sub -> alert). Prototype: an ERROR log line, visible in Cloud Logging.
    events.subscribe("ops.manual_action", lambda e: log.error("MANUAL ACTION REQUIRED: %s for order %s",
                                                              e.get("action"), e.get("order_id")))

    ctx.modes = {
        "store": s.store_backend,
        "messaging": "whatsapp" if s.whatsapp_token and s.whatsapp_phone_number_id else "console",
        "telegram": "bot" if telegram else "off",
        "payment": payment.name, "shipping": shipping.name,
        "ai": f"gemini ({s.gemini_model})" if s.gemini_enabled else "offline rules",
    }
    from . import security
    security.TRUSTED_PROXY_HOPS = max(0, s.trusted_proxy_hops)
    security.validate_settings(s, ctx.modes)
    log.info("Etalase modes: %s", ctx.modes)
    return ctx
