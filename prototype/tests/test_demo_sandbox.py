"""The judge sandbox (DEMO_MODE): it can only run when nothing real is behind it, it seeds a shop that works without setup, its
simulation steps only ever touch the demo seller, and the deploy script keeps real keys out of it. The whole reviewer path over
real HTTP is scripts/e2e_judge.py."""

import importlib.util
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from etalase import demo
from etalase.api import demo_console
from etalase.config import Settings
from etalase.container import build_context
from etalase.gateway import storefront
from etalase.models import Address, OrderStatus, ProductStatus, now
from etalase.security import InsecureConfig, RateLimiter, validate_settings

from .conftest import BUYER, make_product, make_seller

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_MODES = {"payment": "mock", "shipping": "mock", "messaging": "console", "telegram": "off"}


@pytest.fixture
def box():
    """A sandbox context, seeded, the way main.py does it at start-up."""
    ctx = build_context(Settings(etalase_env="dev", demo_mode=True, _env_file=None))
    demo.seed(ctx)
    return ctx


def order_for(ctx, variant_id="var_demo_voal_black", qty=1):
    product = next(p for p in ctx.store.list_products(seller_id=demo.DEMO_SELLER_ID) if any(v.id == variant_id for v in p.variants))
    return ctx.checkout.create_checkout(product.id, variant_id, qty, BUYER, channel="web")


# ---------------------------------------------------------------- the guard
def test_demo_mode_is_off_by_default():
    assert Settings(_env_file=None).demo_mode is False


def test_demo_mode_runs_locally_with_nothing_real_behind_it():
    validate_settings(Settings(etalase_env="dev", demo_mode=True, _env_file=None), SANDBOX_MODES)


@pytest.mark.parametrize("modes, complaint", [
    ({**SANDBOX_MODES, "payment": "xendit"}, "mock payment"),
    ({**SANDBOX_MODES, "shipping": "biteship"}, "mock shipping"),
    ({**SANDBOX_MODES, "messaging": "whatsapp"}, "console messenger"),
    ({**SANDBOX_MODES, "telegram": "bot"}, "Telegram off"),
])
def test_demo_mode_refuses_anything_real(modes, complaint):
    with pytest.raises(InsecureConfig, match=complaint):
        validate_settings(Settings(etalase_env="dev", demo_mode=True, _env_file=None), modes)


def test_the_sandbox_never_runs_with_sign_up_on():
    """With the mock provider a new shop is verified at once, so sign-up on a public sandbox would let anyone open shops."""
    with pytest.raises(InsecureConfig, match="SIGNUP_ENABLED"):
        validate_settings(Settings(etalase_env="dev", demo_mode=True, signup_enabled=True, _env_file=None), SANDBOX_MODES)
    validate_settings(Settings(etalase_env="dev", signup_enabled=True, _env_file=None), {**SANDBOX_MODES})


def test_prod_can_never_be_a_sandbox():
    prod = Settings(etalase_env="prod", demo_mode=True, session_secret="s" * 40, webhook_token="w" * 20, _env_file=None)
    with pytest.raises(InsecureConfig, match="DEMO_MODE only runs with ETALASE_ENV=dev"):
        validate_settings(prod, SANDBOX_MODES)


def test_a_public_sandbox_still_needs_its_tokens():
    public = Settings(etalase_env="dev", demo_mode=True, public_base_url="https://etalase-demo.run.app", _env_file=None)
    with pytest.raises(InsecureConfig, match="DEV_TOKEN"):
        validate_settings(public, SANDBOX_MODES)


def test_the_context_refuses_to_build_with_a_real_whatsapp_token():
    with pytest.raises(InsecureConfig, match="console messenger"):
        build_context(Settings(etalase_env="dev", demo_mode=True, whatsapp_token="t", whatsapp_phone_number_id="1",
                               whatsapp_app_secret="a", whatsapp_verify_token="v" * 20, _env_file=None))


# ---------------------------------------------------------------- the seed
def test_the_seed_is_a_shop_that_works_without_setup(box):
    seller = box.store.get_seller(demo.DEMO_SELLER_ID)
    assert seller.kyc_status == "verified" and seller.whatsapp == demo.DEMO_NUMBER
    products = box.store.list_products(seller_id=demo.DEMO_SELLER_ID, status=ProductStatus.PUBLISHED)
    assert {p.title for p in products} == {"Voal Hijab", "Silk Pashmina", "Linen Shirt"}
    assert all(p.images and p.readiness_score >= 85 for p in products)
    found = box.catalog.search("black voal hijab")
    assert found and found[0]["title"] == "Voal Hijab"
    black = next(v for v in found[0]["variants"] if v["options"] == {"color": "Black"})
    assert black["availability"] == "in_stock"


def test_one_item_is_nearly_sold_out_so_the_seller_is_asked_first(box):
    view = next(r for r in box.catalog.search("silk pashmina"))
    cream = next(v for v in view["variants"] if v["options"] == {"color": "Cream"})
    assert cream["availability"] == "confirm_with_seller"
    order = order_for(box, "var_demo_pashmina_cream")
    assert order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION


def test_seeding_twice_changes_nothing_and_ids_are_stable(box):
    ids = sorted(p.id for p in box.store.list_products(seller_id=demo.DEMO_SELLER_ID))
    demo.seed(box)
    assert sorted(p.id for p in box.store.list_products(seller_id=demo.DEMO_SELLER_ID)) == ids == sorted(demo.SEED_PRODUCT_IDS)


def test_a_real_checkout_works_on_the_seed(box):
    order = order_for(box, qty=2)
    assert order.status == OrderStatus.AWAITING_PAYMENT and order.total > 2 * 85_000


# ---------------------------------------------------------------- keeping the sandbox alive
def test_stock_is_topped_up_and_reconfirmed(box):
    store = box.store
    store.set_stock(demo.DEMO_SELLER_ID, "prd_demo_voal", "var_demo_voal_black", 3, "chat")
    store.set_stock(demo.DEMO_SELLER_ID, "prd_demo_voal", "var_demo_voal_navy", 0, "chat")
    demo.keep_fresh(box)
    voal = store.get_product("prd_demo_voal")
    assert {v.options["color"]: v.stock.on_hand for v in voal.variants} == {"Black": 60, "Navy": 60, "Mocca": 60}

    old = now() - timedelta(days=3)
    store._products["prd_demo_linen"].variants[0].stock.last_confirmed_at = old     # put_product never changes stock, so age the stored copy
    demo.keep_fresh(box)
    assert store.get_product("prd_demo_linen").variants[0].stock.last_confirmed_at > old
    assert box.catalog.search("linen shirt")[0]["variants"][0]["availability"] == "in_stock"


def test_a_visitor_holding_the_stock_cannot_empty_the_shop(box):
    box.store.reserve("ord_hold", "prd_demo_voal", "var_demo_voal_black", 55, now() + timedelta(minutes=15))    # 55 of 60 held by unpaid checkouts
    black = lambda: next(v for v in box.store.get_product("prd_demo_voal").variants if v.options == {"color": "Black"}).stock
    assert black().available == 5
    demo.keep_fresh(box)
    assert black().available == 60 and black().reserved == 55            # the hold is untouched; the shop just has stock again


def test_the_scarce_item_stays_scarce_and_comes_back(box):
    box.store.set_stock(demo.DEMO_SELLER_ID, "prd_demo_pashmina", "var_demo_pashmina_cream", 0, "chat")
    demo.keep_fresh(box)
    cream = next(v for v in box.store.get_product("prd_demo_pashmina").variants if v.options == {"color": "Cream"})
    assert cream.stock.on_hand == 2


def test_what_a_visitor_publishes_disappears_after_an_hour(box):
    mine = make_product(box, box.store.get_seller(demo.DEMO_SELLER_ID), "Plain scarf, grey 6, olive 4, price 60k")
    assert mine.status == ProductStatus.PUBLISHED
    demo.keep_fresh(box)
    assert box.store.get_product(mine.id).status == ProductStatus.PUBLISHED        # too new
    box.store._products[mine.id].updated_at = now() - timedelta(minutes=demo.VISITOR_PRODUCT_MINUTES + 1)    # put_product stamps "now", so age the stored copy
    demo.keep_fresh(box)
    assert box.store.get_product(mine.id).status == ProductStatus.DRAFT
    assert all(box.store.get_product(i).status == ProductStatus.PUBLISHED for i in demo.SEED_PRODUCT_IDS)


def test_the_outbox_cannot_grow_without_end(box):
    for i in range(demo.MAX_OUTBOX + 50):
        box.messenger.send_text(demo.DEMO_NUMBER, f"m{i}")
    demo.keep_fresh(box)
    assert len(box.messenger.outbox) == demo.MAX_OUTBOX and box.messenger.outbox[-1].text == f"m{demo.MAX_OUTBOX + 49}"


# ---------------------------------------------------------------- simulation steps
def test_the_steps_run_in_order_and_only_when_they_apply(box):
    order = order_for(box)
    assert demo.actions_for(order) == ["pay"]
    with pytest.raises(demo.DemoError):
        demo.simulate(box, order.id, "deliver")
    order = demo.simulate(box, order.id, "pay")
    assert order.status == OrderStatus.PAID and demo.actions_for(order) == []
    with pytest.raises(demo.DemoError):
        demo.simulate(box, order.id, "pay")                    # a second payment is not a thing
    box.checkout.seller_accepts(demo.DEMO_SELLER_ID, order.id)
    box.checkout.mark_shipped(demo.DEMO_SELLER_ID, order.id, "JNE123456")
    assert demo.actions_for(box.store.get_order(order.id)) == ["deliver"]
    order = demo.simulate(box, order.id, "deliver")
    assert order.status == OrderStatus.DELIVERED and demo.actions_for(order) == ["release"]
    assert order.payout is None                                # held while the window is open
    order = demo.simulate(box, order.id, "release")
    assert order.status == OrderStatus.PAID_OUT and order.payout.status == "released"


def test_the_console_shows_where_a_buyer_is_not_who(web, box):
    order_for(box)
    state = web.get("/demo/api/state").json()
    assert state["orders"][0]["city"] == BUYER.city
    assert BUYER.name not in str(state) and BUYER.phone not in str(state)


def test_the_cart_checkout_of_the_demo_shop_asks_for_made_up_details_too(box):
    page = storefront.cart_form(box, demo.DEMO_SELLER_ID, "var_demo_voal_black:1").body.decode()
    assert "Demo sandbox." in page and "made-up details" in page


def test_the_checkout_of_the_demo_shop_asks_for_made_up_details(box):
    page = storefront.buy_form(box, demo.DEMO_SELLER_ID, "var_demo_voal_black").body.decode()
    assert "Demo sandbox." in page and "made-up details" in page
    other = make_seller(box, "Toko Lain", "6281234567222")
    product = make_product(box, other)
    assert "made-up details" not in storefront.buy_form(box, other.id, product.variants[0].id).body.decode()


def test_an_unknown_action_is_refused(box):
    order = order_for(box)
    with pytest.raises(demo.DemoError):
        demo.simulate(box, order.id, "teleport")


def test_another_sellers_order_cannot_be_touched(box):
    other = make_seller(box, "Toko Lain", "6281234567111")
    product = make_product(box, other)
    order = box.checkout.create_checkout(product.id, product.variants[0].id, 1, BUYER, channel="web")
    for action in ("pay", "deliver", "release"):
        with pytest.raises(KeyError):
            demo.simulate(box, order.id, action)
    assert box.store.get_order(order.id).status == OrderStatus.AWAITING_PAYMENT
    assert demo.pay_button(box, order) == "" and demo.pay_link(box, order) == ""
    with pytest.raises(KeyError):
        demo.simulate(box, "ord_missing", "pay")


def test_outside_demo_mode_nothing_is_offered():
    ctx = build_context(Settings(etalase_env="dev", _env_file=None))
    seller = make_seller(ctx, "Toko Demo Rina")
    seller.id = demo.DEMO_SELLER_ID                              # even a shop with the demo id
    ctx.store.put_seller(seller)
    product = make_product(ctx, seller)
    order = ctx.checkout.create_checkout(product.id, product.variants[0].id, 1, BUYER, channel="web")
    assert demo.pay_button(ctx, order) == "" and demo.pay_link(ctx, order) == ""
    with pytest.raises(KeyError):
        demo.simulate(ctx, order.id, "pay")


def test_the_order_page_carries_the_button_only_while_it_makes_sense(box):
    order = order_for(box)
    page = storefront.order_page(box, order.id).body.decode()
    assert "Simulate the QRIS payment" in page and f'action="/demo/pay/{order.id}"' in page
    # The page script (gateway/pages.py) posts a form in place only when its body is not empty; a form with no field fell back to a
    # GET and the button did nothing in a real browser. So the form must carry a field.
    assert 'name=demo value=1' in page
    demo.simulate(box, order.id, "pay")
    assert "Simulate the QRIS payment" not in storefront.order_page(box, order.id).body.decode()


# ---------------------------------------------------------------- the console
@pytest.fixture
def web(box):
    app = FastAPI()
    app.include_router(demo_console.build_router(box, RateLimiter()))
    with TestClient(app) as client:
        yield client


def test_the_thread_shows_both_sides_in_order(web, box):
    web.post("/demo/api/send", json={"photo": "voal-hijab.png", "text": "Hijab voal, hitam 5, navy 3, mocca 10, harga 85rb"})
    web.post("/demo/api/send", json={"button_id": "publish", "text": "Publish"})
    chat = web.get("/demo/api/state").json()["chat"]
    assert [m["who"] for m in chat] == ["me", "etalase", "me", "etalase"]
    assert chat[0]["photo"] == "voal-hijab.png" and chat[2]["tapped"] is True
    assert chat[1]["buttons"][0] == {"id": "publish", "title": "Publish"}


def test_a_photo_must_be_one_of_the_samples(web):
    assert web.post("/demo/api/send", json={"photo": "../../etc/passwd"}).status_code == 400
    assert web.post("/demo/api/send", json={"photo": "thank-you.png"}).status_code == 400
    assert web.post("/demo/api/send", json={}).status_code == 400


def test_messages_are_scoped_to_the_demo_number(web, box):
    box.messenger.send_text("6281999999999", "someone else's sign-in code 123456")
    state = web.get("/demo/api/state").json()
    assert "123456" not in str(state)


def test_the_console_runs_an_order_and_refuses_the_wrong_step(web, box):
    order = order_for(box)
    state = web.get("/demo/api/state").json()
    assert state["orders"][0]["id"] == order.id and [a["id"] for a in state["orders"][0]["actions"]] == ["pay"]
    assert state["orders"][0]["next"] == "Waiting for the buyer to pay."
    assert web.post(f"/demo/api/orders/{order.id}/deliver").status_code == 409
    assert web.post(f"/demo/api/orders/{order.id}/pay").status_code == 200
    assert box.store.get_order(order.id).status == OrderStatus.PAID
    assert web.post("/demo/api/orders/ord_missing/pay").status_code == 404


def test_the_order_page_button_redirects_back_whatever_happens(web, box):
    order = order_for(box)
    r = web.post(f"/demo/pay/{order.id}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/pay/{order.id}"
    assert box.store.get_order(order.id).status == OrderStatus.PAID
    assert web.post(f"/demo/pay/{order.id}", follow_redirects=False).status_code == 303      # twice: same page, no error
    assert web.post("/demo/pay/ord_missing", follow_redirects=False).status_code == 303


def test_signing_in_gives_the_demo_seller_and_nobody_else(web, box):
    token = web.post("/demo/api/signin").json()["token"]
    assert box.auth.seller_from_token(token) == demo.DEMO_SELLER_ID


def test_cross_site_posts_are_refused(web):
    evil = {"Origin": "https://evil.example"}
    assert web.post("/demo/api/send", json={"text": "hi"}, headers=evil).status_code == 403
    assert web.post("/demo/api/signin", headers=evil).status_code == 403
    assert web.post("/demo/pay/ord_x", headers=evil).status_code == 403
    assert web.post("/demo/api/orders/ord_x/pay", headers=evil).status_code == 403


def test_the_console_page_renders_and_is_locked_down(web):
    r = web.get("/demo")
    assert r.status_code == 200 and "Try the whole story" in r.text and "@@" not in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"


def test_the_send_route_is_rate_limited(box):
    app = FastAPI()
    app.include_router(demo_console.build_router(box, RateLimiter()))
    with TestClient(app) as client:
        codes = {client.post("/demo/api/send", json={"text": "my shop link"}).status_code for _ in range(160)}
    assert 429 in codes and 200 in codes


# ---------------------------------------------------------------- the deploy
@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("env_to_cloud_demo", ROOT / "scripts" / "env_to_cloud.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["env_to_cloud_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


SECRET_VALUES = ("AIza-SECRET-VALUE", "wa-SECRET", "app-SECRET", "xnd_SECRET", "cb-SECRET", "bs-SECRET", "tg-SECRET", "ig-SECRET",
                 "tgsecret-0123456789")
REAL_ENV = ["GOOGLE_API_KEY=AIza-SECRET-VALUE", "WHATSAPP_TOKEN=wa-SECRET", "WHATSAPP_APP_SECRET=app-SECRET",
            "WHATSAPP_PHONE_NUMBER_ID=1234567890", "XENDIT_SECRET_KEY=xnd_SECRET", "XENDIT_CALLBACK_TOKEN=cb-SECRET",
            "BITESHIP_API_KEY=bs-SECRET", "TELEGRAM_BOT_TOKEN=tg-SECRET", "TELEGRAM_BOT_USERNAME=etalase_bot",
            "TELEGRAM_WEBHOOK_SECRET=tgsecret-0123456789", "INSTAGRAM_APP_ID=1", "INSTAGRAM_APP_SECRET=ig-SECRET", "SELLER_LOCALE=id"]


def test_the_sandbox_deploy_takes_nothing_real_from_the_env_file(tool, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("\n".join(REAL_ENV) + "\n")
    code = tool.main(["--project", "p", "--env-file", str(env), "--dry-run", "--demo", "--fixed", "DEMO_MODE=true"])
    out = capsys.readouterr()
    lines = dict(line.split("=", 1) for line in out.out.splitlines() if "=" in line)
    assert code == 0
    assert lines["SECRETS"] == "GOOGLE_API_KEY=etalase-google-api-key:latest"
    plain = dict(item.split("=", 1) for item in lines["ENV_VARS"].split(lines["ENV_DELIM"]))
    assert plain["DEMO_MODE"] == "true" and plain["SELLER_LOCALE"] == "id"
    assert not {"WHATSAPP_PHONE_NUMBER_ID", "INSTAGRAM_APP_ID", "TELEGRAM_BOT_USERNAME"} & set(plain)
    assert "skipped" in out.err
    assert not any(value in out.err + out.out for value in SECRET_VALUES)      # names are listed, values never


def test_without_the_flag_a_real_deployment_is_unchanged(tool, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("\n".join(REAL_ENV) + "\n")
    tool.main(["--project", "p", "--env-file", str(env), "--dry-run"])
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines() if "=" in line)
    assert "WHATSAPP_TOKEN=etalase-whatsapp-token:latest" in lines["SECRETS"].split(",")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_deploy_script_plans_a_separate_sandbox_service(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(REAL_ENV) + "\n")
    base = {**os.environ, "DRY_RUN": "1", "PROJECT_ID": "demo-proj", "ENV_FILE": str(env_file), "PATH": "/usr/bin:/bin"}
    script = str(ROOT / "scripts" / "deploy_cloud_run.sh")
    done = subprocess.run(["bash", script], cwd=ROOT, text=True, capture_output=True, env={**base, "DEMO": "1"}, timeout=60)
    text = done.stdout + done.stderr
    assert done.returncode == 0, text
    assert "gcloud run deploy etalase-demo" in text and "DEMO_MODE=true" in text and "/demo" in text
    assert "etalase-whatsapp-token" not in text and "etalase-xendit-key" not in text and "etalase-telegram-bot-token" not in text
    assert "etalase-google-api-key" in text and not any(value in text for value in SECRET_VALUES)
    for bad in ({"ETALASE_ENV": "prod"}, {"STORE_BACKEND": "firestore"}):
        refused = subprocess.run(["bash", script], cwd=ROOT, text=True, capture_output=True, env={**base, "DEMO": "1", **bad}, timeout=60)
        assert refused.returncode != 0 and "DEMO=1" in refused.stdout + refused.stderr


# ---------------------------------------------------------------- the shop address
def _seeded(**settings):
    ctx = build_context(Settings(etalase_env="dev", demo_mode=True, _env_file=None, **settings))
    demo.seed(ctx)
    return ctx


def test_demo_shop_has_no_address_without_a_base_domain():
    ctx = _seeded()
    assert ctx.store.get_seller(demo.DEMO_SELLER_ID).slug is None


def test_demo_shop_answers_on_its_own_address_when_a_base_domain_is_set():
    from etalase.seller import slugs
    ctx = _seeded(store_base_domain="localhost:8080", public_base_url="http://localhost:8080")
    seller = ctx.store.get_seller(demo.DEMO_SELLER_ID)
    assert seller.slug == "demo"
    assert slugs.store_url(ctx.settings, seller) == "http://demo.localhost:8080"
    assert ctx.store.find_seller_by_slug("demo").id == demo.DEMO_SELLER_ID


def test_seeding_again_keeps_the_address():
    ctx = _seeded(store_base_domain="localhost:8080", public_base_url="http://localhost:8080")
    demo.seed(ctx)
    assert ctx.store.get_seller(demo.DEMO_SELLER_ID).slug == "demo"


def test_demo_address_falls_back_to_the_shop_name_when_demo_is_the_platform_host():
    ctx = _seeded(store_base_domain="localhost:8080", public_base_url="http://demo.localhost:8080", session_secret="x" * 32,
                   dev_token="d" * 16, webhook_token="w" * 16)
    assert ctx.store.get_seller(demo.DEMO_SELLER_ID).slug == "toko-demo-rina"


def test_demo_card_links_stay_on_the_shared_host_without_a_base_domain():
    links = demo.console_state(_seeded())["links"]
    base = "http://localhost:8080"
    assert links == {"store": f"{base}/stores/{demo.DEMO_SELLER_ID}", "mcp": f"{base}/mcp/",
                     "ucp": f"{base}/.well-known/ucp", "dashboard": f"{base}/"}


def test_demo_card_sends_agents_to_the_shops_own_address_and_keeps_the_dashboard_on_the_shared_host():
    ctx = _seeded(store_base_domain="localhost:8080", public_base_url="http://localhost:8080")
    links = demo.console_state(ctx)["links"]
    shop = "http://demo.localhost:8080"
    assert links["store"] == shop and links["mcp"] == f"{shop}/mcp/" and links["ucp"] == f"{shop}/.well-known/ucp"
    assert links["dashboard"] == "http://localhost:8080/"      # the dashboard is not part of a shop's address
