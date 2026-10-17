"""Sign-up in chat: a stranger builds a shop in the conversation and picks a unique address first. The shop stays invisible
until it is verified, and an address that is never verified goes back to the pool."""

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from etalase.adapters.messaging import Inbound
from etalase.api.main import app
from etalase.api.main import ctx as app_ctx
from etalase.config import Settings
from etalase.container import build_context
from etalase.models import now
from etalase.security import InsecureConfig, validate_settings
from etalase.seller import slugs
from etalase.seller.signup import parse_address

from .conftest import make_seller

DOMAIN = "etalase.test"
_ids = iter(range(1, 10_000))


def make_ctx(**kw):
    kw = {"signup_enabled": True, "store_base_domain": DOMAIN, "public_base_url": "https://app.etalase.test",
          "dev_token": "d" * 20, "webhook_token": "w" * 20, "session_secret": "s" * 40, **kw}      # a non-local URL needs its secrets
    return build_context(Settings(etalase_env="dev", _env_file=None, **kw))


@pytest.fixture
def sctx():
    return make_ctx()


def say(ctx, number, text=None, button=None, kind=None):
    """One message from `number`; returns what Etalase sent back to that number."""
    start = len(ctx.messenger.outbox)
    if button:
        msg = Inbound(number, "button", button_id=button, message_id=f"m{next(_ids)}")
    elif kind:
        msg = Inbound(number, kind, media_id="samples/voal-hijab.png", message_id=f"m{next(_ids)}")
    else:
        msg = Inbound(number, "text", text=text, message_id=f"m{next(_ids)}")
    asyncio.run(ctx.inbox.handle(msg))
    return [m for m in ctx.messenger.outbox[start:] if m.to == number]


def last(replies):
    return replies[-1].text if replies else ""


def seller_of(ctx, number):
    return ctx.store.find_seller_by_whatsapp(number)


def sign_up(ctx, number, name="Toko Rina Hijab", address="Jl. Dago 10, Bandung 40135"):
    say(ctx, number, "hi")
    say(ctx, number, name)
    say(ctx, number, button="signup_use")
    return say(ctx, number, address)


# ---------------------------------------------------------------- the conversation
def test_a_stranger_builds_a_shop_in_chat_and_chooses_the_address_first(sctx):
    n = "6281300000001"
    welcome = say(sctx, n, "hi")
    assert "shop's name" in last(welcome) and "never asks for a code" in last(welcome)      # the scam warning comes first
    assert seller_of(sctx, n).onboarding == "name" and seller_of(sctx, n).kyc_status == "pending"

    offer = say(sctx, n, "Toko Rina Hijab")
    assert "https://toko-rina-hijab.etalase.test" in last(offer) and offer[-1].buttons == [("signup_use", "Use it")]
    assert seller_of(sctx, n).slug is None                       # offered, not taken yet

    asked = say(sctx, n, button="signup_use")
    assert seller_of(sctx, n).slug == "toko-rina-hijab" and "Where will you ship from" in last(asked)

    done = say(sctx, n, "Jl. Dago 10, Bandung 40135")
    s = seller_of(sctx, n)
    assert (s.name, s.onboarding, s.slug) == ("Toko Rina Hijab", "", "toko-rina-hijab")
    assert (s.origin.line1, s.origin.city, s.origin.postal_code) == ("Jl. Dago 10", "Bandung", "40135")
    assert s.kyc_status == "verified" and s.open_for_buyers      # the mock payment provider has no identity check to wait for
    assert "https://toko-rina-hijab.etalase.test" in last(done)
    assert sctx.store.find_seller_by_slug("toko-rina-hijab").id == s.id


def test_after_sign_up_the_same_chat_builds_the_catalog(sctx):
    n = "6281300000002"
    sign_up(sctx, n)
    shown = say(sctx, n, "Hijab voal, hitam 5, navy 3, harga 85rb")
    assert "Publish" in [b[1] for b in shown[-1].buttons]
    assert "https://toko-rina-hijab.etalase.test" in last(say(sctx, n, button="publish"))
    assert sctx.catalog.search("hijab voal")                    # an agent can find it


def test_the_seller_can_type_another_name_instead_of_the_proposal(sctx):
    n = "6281300000003"
    say(sctx, n, "hi")
    say(sctx, n, "Toko Rina Hijab")
    reply = say(sctx, n, "Rina Hijab Bandung")                   # a sentence becomes an address
    assert seller_of(sctx, n).slug == "rina-hijab-bandung" and "Where will you ship from" in last(reply)


def test_a_taken_address_gets_an_alternative_and_exactly_one_shop_wins(sctx):
    a, b = "6281300000004", "6281300000005"
    sign_up(sctx, a)
    say(sctx, b, "hi")
    offer = say(sctx, b, "Toko Rina Hijab")                      # b is offered the next free one straight away
    assert "toko-rina-hijab-2.etalase.test" in last(offer)
    taken = say(sctx, b, "toko-rina-hijab")                      # and typing a's address is refused with a new suggestion
    assert "already taken" in last(taken) and "toko-rina-hijab-2" in last(taken) and seller_of(sctx, b).slug is None
    say(sctx, b, button="signup_use")
    assert seller_of(sctx, b).slug == "toko-rina-hijab-2" and seller_of(sctx, a).slug == "toko-rina-hijab"


def test_two_shops_cannot_hold_one_address_even_when_both_were_offered_it(sctx):
    a, b = "6281300000006", "6281300000007"
    for n in (a, b):
        say(sctx, n, "hi")
        say(sctx, n, "Kopi Lereng")                              # both are offered kopi-lereng: nobody has claimed it yet
    say(sctx, a, button="signup_use")
    reply = say(sctx, b, button="signup_use")                    # b's tap comes second
    assert seller_of(sctx, a).slug == "kopi-lereng" and seller_of(sctx, b).slug is None
    assert "kopi-lereng-2" in last(reply) and "already taken" in last(reply)


@pytest.mark.parametrize("name", ["ab", "x" * 61, "Call me 0812 3456 7890", "rina@shop", "www.rina.com", "https://evil.example",
                                  "BCA Resmi", "Official Store", "Etalase Support", "Toko Xendit"])
def test_names_that_are_links_numbers_or_lookalikes_are_refused(sctx, name):
    n = f"62813100{next(_ids):05d}"
    say(sctx, n, "hi")
    reply = say(sctx, n, name)
    s = seller_of(sctx, n)
    assert s.onboarding == "name" and s.name == "" and s.slug is None
    assert reply and "Where" not in last(reply)


@pytest.mark.parametrize("typed", ["www", "api", "bca-resmi", "admin", "!!!"])
def test_addresses_that_are_reserved_or_lookalikes_are_refused(sctx, typed):
    n = f"62813200{next(_ids):05d}"
    say(sctx, n, "hi")
    say(sctx, n, "Warung Bu Ani")
    say(sctx, n, typed)
    assert seller_of(sctx, n).slug is None and seller_of(sctx, n).onboarding == "slug"


def test_a_photo_before_the_shop_exists_is_not_downloaded(sctx):
    n = "6281300000008"
    say(sctx, n, kind="image")                                   # a stranger's first message is a picture
    assert seller_of(sctx, n).onboarding == "name" and not sctx.inbox.pending_media
    say(sctx, n, kind="image")                                   # and one in the middle of the questions is answered, not stored
    assert not sctx.inbox.pending_media and seller_of(sctx, n).onboarding == "name"


def test_without_a_base_domain_the_address_step_is_skipped():
    ctx = make_ctx(store_base_domain=None)
    n = "6281300000009"
    say(ctx, n, "hi")
    reply = say(ctx, n, "Toko Rina Hijab")
    assert "Where will you ship from" in last(reply) and seller_of(ctx, n).onboarding == "address"
    say(ctx, n, "Jl. Dago 10, Bandung 40135")
    s = seller_of(ctx, n)
    assert s.slug is None and s.open_for_buyers and f"/stores/{s.id}" in last([m for m in ctx.messenger.outbox if m.to == n])


@pytest.mark.parametrize("text,expected", [
    ("Jl. Dago 10, Bandung 40135", ("Jl. Dago 10", "Bandung", "40135")),
    ("Jl. Dago 10 No. 3, Bandung, 40135", ("Jl. Dago 10 No. 3", "Bandung", "40135")),
    ("40135 Jl. Dago 10, Bandung", ("Jl. Dago 10", "Bandung", "40135")),
    ("Jl Sudirman 5, Jakarta Selatan 12190", ("Jl Sudirman 5", "Jakarta Selatan", "12190")),
    ("Bandung 40135", None), ("Jl. Dago 10, Bandung", None), ("Jl. Dago 10, Bandung 4013", None),
    ("Jl. Dago 10, Bandung 401355", None), ("Jl. A 10000, B 12345", None), ("", None), ("x" * 300 + " 40135", None),
])
def test_the_pickup_address_is_read_from_one_message(text, expected):
    assert parse_address(text) == expected


# ---------------------------------------------------------------- not verified yet
@pytest.fixture
def real_money(sctx, monkeypatch):
    """A deployment with a real payment provider: nothing verifies a shop by itself."""
    monkeypatch.setattr(sctx.payment, "name", "xendit")
    return sctx


def test_with_a_real_payment_provider_the_new_shop_waits_for_verification(real_money):
    ctx, n = real_money, "6281300000010"
    done = sign_up(ctx, n)
    s = seller_of(ctx, n)
    assert s.kyc_status == "pending" and not s.open_for_buyers and s.slug == "toko-rina-hijab"
    assert "not open to buyers yet" in last(done) and "72 hours" in last(done)
    saved = say(ctx, n, "Hijab voal, hitam 5, navy 3, harga 85rb")
    assert "Publish" in [b[1] for b in saved[-1].buttons]       # the catalog still takes shape in the chat...
    published = say(ctx, n, button="publish")
    assert "not open to buyers yet" in last(published) and "https://" not in last(published)    # ...but no link goes out
    assert ctx.catalog.search("hijab voal") == []                # invisible to agents
    assert ctx.store.list_products(seller_id=s.id, status="published")      # saved, ready for the day the shop opens


def test_the_operator_verifies_and_the_shop_opens_at_once(real_money):
    ctx, n = real_money, "6281300000011"
    sign_up(ctx, n)
    say(ctx, n, "Hijab voal, hitam 5, navy 3, harga 85rb")
    say(ctx, n, button="publish")
    s = seller_of(ctx, n)
    before = len(ctx.messenger.outbox)
    opened = ctx.signup.open_shop(s.id)
    assert opened.open_for_buyers and "verified and open" in ctx.messenger.outbox[before].text
    assert ctx.catalog.search("hijab voal")                          # and agents can find it from this moment


def test_an_address_never_verified_goes_back_to_the_pool(real_money):
    ctx, a, b = real_money, "6281300000012", "6281300000013"
    sign_up(ctx, a)
    assert ctx.signup.sweep(now() + timedelta(hours=71)) == []                       # still held
    before = len(ctx.messenger.outbox)
    assert ctx.signup.sweep(now() + timedelta(hours=73)) == ["toko-rina-hijab"]
    assert seller_of(ctx, a).slug is None and "given back" in ctx.messenger.outbox[before].text
    say(ctx, b, "hi")                                                                # free for anyone at once, not after 30 days
    assert "https://toko-rina-hijab.etalase.test" in last(say(ctx, b, "Toko Rina Hijab"))
    say(ctx, b, button="signup_use")
    assert seller_of(ctx, b).slug == "toko-rina-hijab"


def test_a_verified_shop_keeps_its_address_for_good(sctx):
    n = "6281300000014"
    sign_up(sctx, n)
    assert sctx.signup.sweep(now() + timedelta(days=365)) == [] and seller_of(sctx, n).slug == "toko-rina-hijab"


def test_a_seller_who_lost_the_address_picks_one_again_when_they_write(real_money):
    ctx, a, b = real_money, "6281300000015", "6281300000016"
    sign_up(ctx, a)
    ctx.signup.sweep(now() + timedelta(hours=80))
    sign_up(ctx, b)                                                                  # someone else takes it meanwhile
    again = say(ctx, a, "hello?")
    assert seller_of(ctx, a).onboarding == "slug" and "toko-rina-hijab-2" in last(again)
    say(ctx, a, button="signup_use")
    assert seller_of(ctx, a).slug == "toko-rina-hijab-2" and seller_of(ctx, a).onboarding == "address"


def test_the_operator_can_verify_a_seller_whose_hold_ran_out(real_money):
    ctx, a = real_money, "6281300000017"
    sign_up(ctx, a)
    ctx.signup.sweep(now() + timedelta(hours=80))
    opened = ctx.signup.open_shop(seller_of(ctx, a).id)
    assert opened.open_for_buyers and opened.slug                                    # given a fresh address on the way in


# ---------------------------------------------------------------- limits
def test_sign_ups_are_capped_per_hour_across_the_platform():
    ctx = make_ctx(signup_shops_per_hour=2)
    for i in range(2):
        say(ctx, f"628133000010{i}", "hi")
    reply = say(ctx, "6281330000103", "hi")
    assert "try again in an hour" in last(reply) and seller_of(ctx, "6281330000103") is None
    assert len(ctx.store.pending_sellers()) == 2


def test_one_number_is_one_shop_however_often_it_says_hi(sctx):
    n = "6281300000018"
    for _ in range(3):
        say(sctx, n, "hi")
    assert len([s for s in sctx.store.pending_sellers() if s.whatsapp == n]) == 1


def test_a_second_first_message_at_the_same_moment_does_not_make_a_second_shop(sctx):
    other = "6281300000020"
    sctx.store.create_doc("signup_number", other, {"seller_id": "sel_winner"})      # the request that got there first
    assert sctx.signup.begin(Inbound(other, "text", text="hi", message_id="a2")) is True
    assert seller_of(sctx, other) is None                            # this one lost the race, so it made nothing


def test_sign_up_is_off_by_default_and_a_stranger_is_pointed_to_the_dashboard():
    ctx = build_context(Settings(etalase_env="dev", _env_file=None))
    reply = say(ctx, "6281300000021", "hi")
    assert "Sign up at" in last(reply) and seller_of(ctx, "6281300000021") is None


def test_telegram_strangers_never_start_a_shop(sctx):
    say(sctx, "tg:12345", "hi")
    assert sctx.store.pending_sellers() == []


def test_turning_sign_up_on_in_prod_needs_an_operator_token():
    modes = {"messaging": "whatsapp", "payment": "xendit", "shipping": "biteship", "store": "firestore", "ai": "gemini"}
    base = dict(etalase_env="prod", _env_file=None, session_secret="s" * 40, webhook_token="w" * 20, signup_enabled=True,
                whatsapp_token="t", whatsapp_phone_number_id="1", whatsapp_app_secret="a", whatsapp_verify_token="v" * 12,
                xendit_secret_key="k", xendit_callback_token="c", biteship_api_key="b")
    with pytest.raises(InsecureConfig, match="OPERATOR_TOKEN"):
        validate_settings(Settings(**base), modes)
    validate_settings(Settings(**base, operator_token="o" * 20), modes)


# ---------------------------------------------------------------- the operator's endpoints
OP = {"X-Etalase-Operator-Token": "o" * 20}


@pytest.fixture
def op(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "operator_token", "o" * 20)
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", DOMAIN)
    return TestClient(app)


def test_the_operator_sees_the_queue_and_verifies_a_shop(op):
    seller = make_seller(app_ctx, "Antrean Toko", f"62813400{next(_ids):05d}", kyc="pending")
    slugs.assign(app_ctx, seller.id)
    rows = op.get("/tasks/pending-sellers", headers=OP).json()
    assert seller.id in [r["seller_id"] for r in rows]
    assert op.post("/tasks/verify-seller", json={"seller_id": seller.id}).status_code in (401, 403, 503)      # no token, no luck
    ok = op.post("/tasks/verify-seller", headers=OP, json={"seller_id": seller.id})
    assert ok.status_code == 200 and ok.json()["kyc_status"] == "verified"
    assert seller.id not in [r["seller_id"] for r in op.get("/tasks/pending-sellers", headers=OP).json()]
    assert op.post("/tasks/verify-seller", headers=OP, json={"seller_id": "sel_nope"}).status_code == 404
