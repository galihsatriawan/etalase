"""Wires settings, store, adapters and services together (one place to swap implementations)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .adapters.messaging import ConsoleMessenger, Messenger, WhatsAppCloudMessenger
from .adapters.payment import MockPayment, PaymentProvider, XenditPayment
from .adapters.shipping import BiteshipShipping, MockShipping, ShippingProvider
from .catalog.extractor import GeminiExtractor, HeuristicExtractor
from .catalog.service import CatalogService
from .checkout.service import CheckoutService
from .config import Settings, get_settings
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
    connector: object = None
    auth: object = None
    seller_agent: object = None
    t: object = None
    modes: dict = field(default_factory=dict)


def build_context(settings: Settings | None = None, echo_messages: bool = False) -> AppContext:
    from .auth import OtpAuth
    from .connector.service import ConnectorService
    from .gateway.tools import GatewayTools
    from .seller import notifications
    from .seller.i18n import Translator
    from .seller.inbox import SellerInbox

    s = settings or get_settings()
    events = EventBus()
    store = build_store(s.store_backend, s.google_cloud_project)

    if s.whatsapp_token and s.whatsapp_phone_number_id:
        messenger: Messenger = WhatsAppCloudMessenger(s.whatsapp_token, s.whatsapp_phone_number_id, s.whatsapp_api_version)
    else:
        samples = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
        messenger = ConsoleMessenger(echo=echo_messages, media_roots=[samples])
    payment: PaymentProvider = XenditPayment(s.xendit_secret_key, s.xendit_callback_token) if s.xendit_secret_key else MockPayment()
    shipping: ShippingProvider = BiteshipShipping(s.biteship_api_key) if s.biteship_api_key else MockShipping()
    if s.gemini_enabled:
        extractor = GeminiExtractor(s.gemini_model, s.google_genai_use_vertexai, s.google_cloud_project,
                                    s.google_cloud_location, s.google_api_key)
    else:
        extractor = HeuristicExtractor()

    catalog = CatalogService(store, s, extractor, events)
    checkout = CheckoutService(store, s, catalog, payment, shipping, events)
    media_dir = os.getenv("ETALASE_MEDIA_DIR", "./media")
    ctx = AppContext(settings=s, store=store, events=events, messenger=messenger, payment=payment,
                     shipping=shipping, catalog=catalog, checkout=checkout, media_dir=media_dir)
    ctx.t = Translator(s.seller_locale)
    ctx.gateway = GatewayTools(ctx)
    ctx.inbox = SellerInbox(ctx)
    ctx.connector = ConnectorService(ctx)
    ctx.auth = OtpAuth(ctx, s.session_secret)
    if s.gemini_enabled:
        from .agents.seller_agent import SellerAgentRunner
        ctx.seller_agent = SellerAgentRunner(ctx)

    notifications.register(ctx)
    events.subscribe("order.paid", ctx.connector.deliver_order)
    # Pilot: page the on-call operator (Pub/Sub -> alert). Prototype: an ERROR log line, visible in Cloud Logging.
    events.subscribe("ops.manual_action", lambda e: log.error("MANUAL ACTION REQUIRED: %s for order %s",
                                                              e.get("action"), e.get("order_id")))

    ctx.modes = {
        "store": s.store_backend,
        "messaging": "whatsapp" if isinstance(messenger, WhatsAppCloudMessenger) else "console",
        "payment": payment.name, "shipping": shipping.name,
        "ai": f"gemini ({s.gemini_model})" if s.gemini_enabled else "offline rules",
    }
    from .security import validate_settings
    validate_settings(s, ctx.modes)
    log.info("Etalase modes: %s", ctx.modes)
    return ctx
