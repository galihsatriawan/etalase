"""Runtime settings and per-country configuration ("country packs")."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SESSION_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    # dev enables /dev simulation endpoints; prod disables them
    etalase_env: str = "dev"
    # "memory" (default, no credentials) or "firestore"
    store_backend: str = "memory"
    public_base_url: str = "http://localhost:8080"

    # Gemini. With no key and GOOGLE_GENAI_USE_VERTEXAI unset, Etalase falls back to
    # deterministic offline parsers so the whole flow still runs.
    # Google's docs now say GEMINI_API_KEY (older ones GOOGLE_API_KEY) and GOOGLE_GENAI_USE_ENTERPRISE (older:
    # GOOGLE_GENAI_USE_VERTEXAI); both spellings of each are accepted.
    google_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    google_genai_use_vertexai: bool = Field(
        default=False, validation_alias=AliasChoices("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"))
    google_cloud_project: str | None = None
    google_cloud_location: str = "asia-southeast1"
    # Google limits the 2.5 models to existing users and recommends 3.8 Flash for new projects (ai.google.dev, 2026-10-15)
    gemini_model: str = "gemini-3.8-flash"

    # WhatsApp Business Cloud API (Meta)
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str = "etalase-dev-verify"
    whatsapp_api_version: str = "v26.0"

    # Telegram bot (optional second seller channel, adapters/telegram.py). Create the bot with @BotFather; the secret is
    # any 16+ characters from A-Z a-z 0-9 _ - and is checked on every webhook call.
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_bot_username: str | None = None     # optional; looked up with getMe when empty

    # Instagram API with Instagram Login (catalog import from posts)
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None

    # Payment (Xendit) and shipping (Biteship)
    xendit_secret_key: str | None = None
    xendit_callback_token: str | None = None
    biteship_api_key: str | None = None

    # Optional per-shop addresses: https://<slug>.<STORE_BASE_DOMAIN>. A host, with a port when local (rina.localhost:8080).
    # Needs wildcard DNS and certificate, see SETUP.md. Unset = every shop link stays /stores/<id> on the shared host.
    store_base_domain: str | None = None

    # Language of WhatsApp messages to sellers: "en" (default, demo) or "id" (Bahasa Indonesia)
    seller_locale: str = "en"
    session_secret: str = DEFAULT_SESSION_SECRET

    # Security. On any non-local deployment both are required (see etalase/security.py):
    # DEV_TOKEN protects /dev/* (header X-Dev-Token); WEBHOOK_TOKEN protects webhooks that have
    # no provider signature (shipping, mock payment, console WhatsApp, /tasks/*) via ?token=...
    dev_token: str | None = None
    webhook_token: str | None = None
    whatsapp_app_secret: str | None = None

    # Business rules
    commission_rate: float = 0.04
    reservation_ttl_minutes: int = 15
    confirm_if_stale_days: int = 3
    confirm_if_available_lte: int = 2
    seller_confirm_timeout_minutes: int = 60
    max_qty_per_order: int = 10
    max_checkouts_per_buyer_per_hour: int = 5
    # Payout hold. After the courier confirms delivery the seller's money stays at the payment provider for this long.
    # It is also the buyer's window to ask for a return, so a refund never has to be clawed back from a seller. 0 pays out
    # at once and switches returns off. Sellers with fewer than NEW_SELLER_ORDERS completed orders wait the longer time.
    payout_hold_hours: int = 72
    new_seller_hold_hours: int = 168
    new_seller_orders: int = 5
    # A return request the seller does not answer in this many hours counts as approved; an approved return that has not
    # reached the seller after RETURN_LAPSE_DAYS lapses (or goes to an operator if the buyer gave a tracking number).
    return_seller_response_hours: int = 48
    return_lapse_days: int = 7
    # Gemini calls one seller may trigger per hour (free-text turns plus catalog builds). Only counted when Gemini is on.
    seller_ai_calls_per_hour: int = 60

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.google_api_key) or self.google_genai_use_vertexai

    @property
    def is_dev(self) -> bool:
        return self.etalase_env == "dev"

    @property
    def is_local(self) -> bool:
        host = self.public_base_url.split("://")[-1].split("/")[0].split(":")[0]
        return host in ("localhost", "127.0.0.1", "testserver", "0.0.0.0")


@dataclass(frozen=True)
class CountryPack:
    """Everything that changes per market. A new market = a new entry + any missing adapter."""

    code: str
    currency: str
    locale: str
    payment_provider: str
    shipping_provider: str
    tax_profile: str


COUNTRY_PACKS: dict[str, CountryPack] = {
    "ID": CountryPack("ID", "IDR", "id-ID", payment_provider="xendit", shipping_provider="biteship", tax_profile="id-ppn"),
    # Planned (roadmap phase 4), listed so the shape is visible:
    # "MY": CountryPack("MY", "MYR", "ms-MY", payment_provider="xendit", shipping_provider="tbd", tax_profile="my-sst"),
    # "SG": CountryPack("SG", "SGD", "en-SG", payment_provider="stripe", shipping_provider="tbd", tax_profile="sg-gst"),
}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def export_google_env(s: Settings) -> None:
    """Make Gemini credentials from `.env` visible to ADK and google-genai, which read real environment
    variables. Variables that are already set (Cloud Run, your shell) always win."""
    if s.google_genai_use_vertexai:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        if s.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", s.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", s.google_cloud_location)
    elif s.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", s.google_api_key)
