"""Sign-up in chat: a stranger who writes to the Etalase number builds a shop in the conversation.

    "hi" -> shop name -> shop address (a name of their own on <STORE_BASE_DOMAIN>, unique) -> where they ship from -> done

The shop is created at the first answer and starts as `pending`: the seller can already send photos and descriptions and see
the catalog take shape, but nothing of the shop is visible to buyers or agents until it is verified (Seller.open_for_buyers).
With the mock payment provider (a local run, the sandbox) a finished sign-up is verified at once. With a real one an operator
verifies it (POST /tasks/verify-seller): the payment provider's own identity and bank check, reached through a link, is the
design in docs/02-product.md and is NOT built.

Off unless SIGNUP_ENABLED is set. Limits, because this is the door anyone on the internet can knock on:
- one shop per number (the number is the identity, and it is proven by Meta's signed webhook);
- SIGNUP_SHOPS_PER_HOUR new shops across the platform;
- the address of a shop that is not verified within SIGNUP_HOLD_HOURS goes back to the pool (`sweep`), so names cannot be hoarded;
- shop names and addresses may not read like a bank, a marketplace, an official account or Etalase (slugs.LOOKALIKE_WORDS), and
  may not hold links, handles or phone numbers;
- until a shop is verified its address and store show a holding page and its products are invisible to search.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from ..adapters.messaging import TELEGRAM_PREFIX, Inbound
from ..models import Address, Seller, now
from ..security import RateLimiter
from . import slugs

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.signup")

YES = {"ok", "oke", "okay", "ya", "iya", "yes", "y", "use it", "pakai ini", "pakai", "gas", "setuju"}
POSTAL = re.compile(r"(?<!\d)(\d{5})(?!\d)")
USE_IT = "signup_use"
STEP_MESSAGES_PER_HOUR = 60        # a person typing; a script sending hundreds of messages gets silence


class Signup:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self._limits = RateLimiter()

    @property
    def enabled(self) -> bool:
        return bool(self.ctx.settings.signup_enabled)

    def _say(self, to: str, key: str, buttons: list[tuple[str, str]] | None = None, **kw) -> None:
        text = self.ctx.t(key, **kw)
        if buttons:
            self.ctx.messenger.send_buttons(to, text, buttons)
        else:
            self.ctx.messenger.send_text(to, text)

    # ------------------------------------------------------------------ start
    def begin(self, msg: Inbound) -> bool:
        """A WhatsApp number nobody knows wrote to us. True when this started a sign-up (False: sign-up is off)."""
        if not self.enabled or msg.sender.startswith(TELEGRAM_PREFIX):
            return False
        ctx = self.ctx
        if not self._limits.allow("signup:all", ctx.settings.signup_shops_per_hour, 3600):
            if self._limits.allow(f"signup-busy:{msg.sender}", 2, 3600):
                self._say(msg.sender, "signup_busy")
            return True
        seller = Seller(name="", whatsapp=msg.sender, kyc_status="pending", onboarding="name")
        # Two first messages from one number at the same moment must make one shop, not two.
        if not ctx.store.create_doc("signup_number", msg.sender, {"seller_id": seller.id, "at": now().isoformat()}):
            return True
        ctx.store.put_seller(seller)
        self._say(msg.sender, "signup_welcome")
        return True

    # ------------------------------------------------------------------ the steps
    def step(self, seller: Seller, msg: Inbound) -> None:
        to = msg.sender
        if not self._limits.allow(f"signup-step:{to}", STEP_MESSAGES_PER_HOUR, 3600):
            return
        text = (msg.text or "").strip() if msg.kind == "text" else ""
        button = msg.button_id if msg.kind == "button" else ""
        {"name": self._name, "slug": self._slug, "address": self._address}.get(seller.onboarding, self._resume)(seller, text, button, to)

    def _name(self, seller: Seller, text: str, button: str, to: str) -> None:
        try:
            name = slugs.check_shop_name(text)
        except slugs.SlugError as e:
            if not text:       # a photo or a sticker as the first answer: ask again, plainly
                return self._say(to, "signup_welcome")
            return self._say(to, "signup_name_bad", reason=str(e))
        seller.name = name
        seller.origin = Address(name=name, phone=seller.whatsapp, line1="", city="", postal_code="")
        if not slugs.subdomains_enabled(self.ctx.settings):
            seller.onboarding = "address"
            self.ctx.store.put_seller(seller)
            return self._say(to, "signup_address", saved="")
        seller.onboarding = "slug"
        self.ctx.store.put_seller(seller)
        self._offer(seller, slugs.suggest(self.ctx, name, seller.id), to, "signup_slug_offer", name=name)

    def _offer(self, seller: Seller, proposal: str | None, to: str, key: str, **kw) -> None:
        if proposal is None:        # fifty variations all taken: ask for a name of their own
            return self._say(to, "signup_slug_bad", reason="Every name close to yours is taken.", url="(type a name)")
        self.ctx.store.put_doc("signup", seller.id, {"proposal": proposal})
        url = slugs.store_url(self.ctx.settings, seller.model_copy(update={"slug": proposal}))
        self._say(to, key, [(USE_IT, self.ctx.t("btn_use_it"))], url=url, **kw)

    def _slug(self, seller: Seller, text: str, button: str, to: str) -> None:
        ctx = self.ctx
        proposal = (ctx.store.get_doc("signup", seller.id) or {}).get("proposal")
        if button == USE_IT or text.lower() in YES:
            wanted = proposal
        else:
            wanted = slugs.slugify(text) if re.search(r"[A-Za-z0-9]", text) else None
        if not wanted:
            return self._offer(seller, proposal or slugs.suggest(ctx, seller.name, seller.id), to, "signup_slug_offer", name=seller.name)
        try:
            claimed = slugs.claim(ctx, seller.id, wanted)       # atomic: of two shops asking at once, one wins
        except slugs.SlugError as e:
            if "taken" in str(e):
                alt = slugs.suggest(ctx, wanted, seller.id)
                return self._offer(seller, alt, to, "signup_slug_taken", slug=wanted)
            url = slugs.store_url(ctx.settings, seller.model_copy(update={"slug": proposal})) if proposal else "(type a name)"
            return self._say(to, "signup_slug_bad", [(USE_IT, ctx.t("btn_use_it"))] if proposal else None, reason=str(e), url=url)
        claimed.onboarding = "address"
        ctx.store.put_seller(claimed)
        self._say(to, "signup_address", saved=ctx.t("signup_saved", url=slugs.store_url(ctx.settings, claimed)))

    def _address(self, seller: Seller, text: str, button: str, to: str) -> None:
        parsed = parse_address(text)
        if parsed is None:
            return self._say(to, "signup_address_bad")
        street, city, postal = parsed
        seller.origin = Address(name=seller.name, phone=seller.whatsapp, line1=street, city=city, postal_code=postal)
        seller.onboarding = ""
        if self.ctx.payment.name == "mock":      # a local run or the sandbox: there is no identity check to wait for
            seller.kyc_status, seller.payout_account_id = "verified", "mock-sub-account"
        self.ctx.store.put_seller(seller)
        url = slugs.store_url(self.ctx.settings, seller)
        if seller.open_for_buyers:
            return self._say(to, "signup_done_open", url=url)
        hold = self.ctx.t("signup_hold_note", hours=self.ctx.settings.signup_hold_hours) if seller.slug else ""
        self._say(to, "signup_done_pending", name=seller.name, url=url, hold=hold)

    def _resume(self, seller: Seller, text: str, button: str, to: str) -> None:
        """An unknown step (a record from an older version): start again at the name."""
        seller.onboarding = "name"
        self.ctx.store.put_seller(seller)
        self._say(to, "signup_welcome")

    # ------------------------------------------------------------------ after sign-up
    def reclaim(self, seller: Seller, to: str) -> bool:
        """A shop that is not verified and lost its address to the hold timer starts the address step again when the seller
        writes. True when it did (the message was consumed)."""
        ctx = self.ctx
        if not (self.enabled and slugs.subdomains_enabled(ctx.settings)) or seller.open_for_buyers or seller.slug or seller.onboarding:
            return False
        seller.onboarding = "slug"
        ctx.store.put_seller(seller)
        self._offer(seller, slugs.suggest(ctx, seller.name, seller.id), to, "signup_slug_offer", name=seller.name)
        return True

    def sweep(self, when=None) -> list[str]:
        """The background job: a shop still not verified after SIGNUP_HOLD_HOURS gives its address back (so a name cannot be
        hoarded), and its seller is told."""
        ctx = self.ctx
        cutoff = (when or now()) - timedelta(hours=ctx.settings.signup_hold_hours)
        released = []
        for seller in ctx.store.pending_sellers():
            if not seller.slug or seller.created_at > cutoff:
                continue
            slug = slugs.release_hold(ctx, seller.id)
            if slug is None:
                continue
            released.append(slug)
            try:
                ctx.messenger.send_text(seller.address, ctx.t("signup_released", slug=slug, hours=ctx.settings.signup_hold_hours))
            except Exception:       # the 24-hour window may be closed; the address is back in the pool either way
                log.info("could not tell %s that %s was released", seller.id, slug)
        return released

    def open_shop(self, seller_id: str) -> Seller:
        """An operator verified this seller (identity and bank checked outside Etalase): the shop opens to buyers and agents."""
        ctx = self.ctx
        seller = ctx.store.get_seller(seller_id)
        if seller is None:
            raise KeyError("seller not found")
        seller.kyc_status = "verified"
        if ctx.payment.name == "mock":
            seller.payout_account_id = seller.payout_account_id or "mock-sub-account"
        ctx.store.put_seller(seller)
        if slugs.subdomains_enabled(ctx.settings) and not seller.slug:    # the hold ran out while they waited: take the next free one
            seller = slugs.assign(ctx, seller.id)
        try:
            ctx.messenger.send_notification(seller.address, ctx.t("shop_opened", url=slugs.store_url(ctx.settings, seller)))
        except Exception:
            log.info("could not tell %s that the shop is open (24-hour window closed?)", seller.id)
        return seller


def parse_address(text: str) -> tuple[str, str, str] | None:
    """'Jl. Dago 10, Bandung 40135' -> ('Jl. Dago 10', 'Bandung', '40135'). Needs a street, a city and a 5-digit postal code."""
    text = re.sub(r"\s+", " ", text or "").strip()
    found = POSTAL.findall(text)
    if len(found) != 1 or len(text) > 200:
        return None
    postal = found[0]
    rest = re.sub(r"\s*,?\s*" + postal + r"\s*,?\s*", ", ", text).strip(" ,")
    street, _, city = rest.rpartition(",")
    street, city = street.strip(" ,"), city.strip(" ,")
    if len(street) < 3 or len(city) < 2 or not re.search(r"[A-Za-z]", city) or re.search(r"\d{5}", city):
        return None
    return street, city[:60], postal
