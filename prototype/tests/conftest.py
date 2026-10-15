import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ETALASE_ENV", "dev")
# Tests must not depend on the developer's shell or .env file.
for var in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "XENDIT_SECRET_KEY", "XENDIT_CALLBACK_TOKEN",
            "BITESHIP_API_KEY", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "DEV_TOKEN", "WEBHOOK_TOKEN",
            "PUBLIC_BASE_URL", "SESSION_SECRET", "STORE_BACKEND", "SELLER_LOCALE"):
    os.environ.pop(var, None)
os.chdir(Path(__file__).resolve().parents[1] / "tests")  # no prototype/.env is picked up from here

from etalase.config import Settings  # noqa: E402
from etalase.container import build_context  # noqa: E402
from etalase.models import Address, Seller  # noqa: E402

BUYER = Address(name="Dewi", phone="6285700001111", line1="Jl. Dago 10", city="Bandung", postal_code="40135")


@pytest.fixture
def ctx():
    return build_context(Settings(etalase_env="dev", _env_file=None))


def make_seller(ctx, name="Toko Rina", wa="6281234567890", kyc="verified"):
    seller = Seller(name=name, whatsapp=wa, kyc_status=kyc,
                    origin=Address(name=name, line1="Jl. Contoh 1", city="Jakarta", postal_code="12950"))
    ctx.store.put_seller(seller)
    return seller


def make_product(ctx, seller, text="Voal hijab, black 5, navy 3, mocca 10, price 85k", publish=True):
    drafts, _ = ctx.catalog.ingest(seller.id, [text], ["samples/voal-hijab.png"])
    if publish:
        ctx.catalog.publish(seller.id)
    return ctx.store.get_product(drafts[0].id)


def variant(product, color):
    return next(v for v in product.variants if v.options.get("color") == color)


@pytest.fixture
def seller(ctx):
    return make_seller(ctx)


@pytest.fixture
def product(ctx, seller):
    return make_product(ctx, seller)
