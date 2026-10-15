"""Per-shop addresses (rina.<domain>) instead of /stores/<id>: validation, host mapping, canonical links, and the
guarantee that nothing changes while STORE_BASE_DOMAIN is unset."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app
from etalase.api.main import ctx as app_ctx
from etalase.config import Settings
from etalase.models import now
from etalase.security import DASHBOARD_CSP, InsecureConfig, validate_settings
from etalase.seller import slugs

from .conftest import make_seller

DOMAIN = "etalase.test"


def settings(**kw):
    return Settings(etalase_env="dev", _env_file=None, public_base_url="https://app.etalase.test", **kw)


# ---------------------------------------------------------------- the slug itself
@pytest.mark.parametrize("raw,expected", [("rina", "rina"), ("  Toko-Rina  ", "toko-rina"), ("abc", "abc"), ("a1-b2-c3", "a1-b2-c3")])
def test_good_slugs_are_normalised(raw, expected):
    assert slugs.normalise(raw) == expected


@pytest.mark.parametrize("raw", ["", "ab", "x" * 31, "-rina", "rina-", "ri--na", "ri na", "rina.id", "rina_id", "rína", "xn--rina",
                                 "www", "API", "etalase", "Checkout"])
def test_bad_or_reserved_slugs_are_refused(raw):
    with pytest.raises(slugs.SlugError):
        slugs.normalise(raw)


def test_a_suggestion_comes_from_the_shop_name():
    assert slugs.slugify("Toko Rina Hijab!") == "toko-rina-hijab"
    assert slugs.slugify("Kopi   Lereng  &  Co") == "kopi-lereng-co"
    assert slugs.slugify("Ab") == "ab-shop"
    assert len(slugs.slugify("x" * 80)) <= 30
    assert slugs.slugify("Warung Bu Ani") == "warung-bu-ani"


def test_claiming_and_assigning(ctx):
    a, b = make_seller(ctx, "Toko Rina", "6281000000001"), make_seller(ctx, "Toko Rina", "6281000000002")
    assert slugs.assign(ctx, a.id).slug == "toko-rina"
    assert slugs.assign(ctx, b.id).slug == "toko-rina-2"                       # the next free one
    with pytest.raises(slugs.SlugError, match="taken"):
        slugs.claim(ctx, b.id, "Toko-Rina")                                    # case does not make it different
    assert slugs.claim(ctx, a.id, "toko-rina").slug == "toko-rina"             # re-claiming your own is fine
    slugs.claim(ctx, a.id, "rina")                                             # moving releases the old name...
    with pytest.raises(slugs.SlugError, match="taken"):                        # ...but old links and QR codes still point at it
        slugs.claim(ctx, b.id, "toko-rina")                                    # so nobody else gets it for a while
    assert slugs.claim(ctx, a.id, "toko-rina").slug == "toko-rina"             # the previous owner can always take it back
    slugs.claim(ctx, a.id, "rina")
    ctx.store.update_doc("slug", "toko-rina", lambda d: d.update(released_at=(now() - slugs.RELEASE_COOLDOWN - timedelta(hours=1)).isoformat()) or True)
    assert slugs.claim(ctx, b.id, "toko-rina").slug == "toko-rina"             # after the cooldown it is free for anyone
    assert ctx.store.find_seller_by_slug("rina").id == a.id and ctx.store.find_seller_by_slug("nobody") is None


# ---------------------------------------------------------------- links and hosts
def test_links_stay_paths_until_a_base_domain_is_set(ctx):
    seller = make_seller(ctx)
    seller.slug = "rina"
    assert slugs.store_url(settings(), seller) == f"https://app.etalase.test/stores/{seller.id}"
    assert slugs.store_url(settings(store_base_domain=DOMAIN), seller) == "https://rina.etalase.test"
    seller.slug = None
    assert slugs.store_url(settings(store_base_domain=DOMAIN), seller).endswith(f"/stores/{seller.id}")   # no slug yet
    local = Settings(etalase_env="dev", _env_file=None, public_base_url="http://localhost:8080", store_base_domain="localhost:8080")
    seller.slug = "rina"
    assert slugs.store_url(local, seller) == "http://rina.localhost:8080"


def test_the_host_header_names_the_shop():
    s = settings(store_base_domain=DOMAIN)
    assert slugs.slug_from_host(s, "rina.etalase.test") == "rina"
    assert slugs.slug_from_host(s, "RINA.Etalase.Test") == "rina"
    assert slugs.slug_from_host(s, "etalase.test") is None                    # the bare domain
    assert slugs.slug_from_host(s, "a.b.etalase.test") is None                # one label only
    assert slugs.slug_from_host(s, "app.etalase.test") is None                # the platform's own host
    assert slugs.slug_from_host(s, "rina.evil.example") is None
    assert slugs.slug_from_host(s, "rina.etalase.test.evil.example") is None
    assert slugs.slug_from_host(settings(), "rina.etalase.test") is None      # feature off
    with_port = Settings(etalase_env="dev", _env_file=None, public_base_url="http://localhost:8080", store_base_domain="localhost:8080")
    assert slugs.slug_from_host(with_port, "rina.localhost:8080") == "rina"


def test_the_base_domain_must_be_a_bare_host():
    modes = {"messaging": "console", "payment": "mock", "shipping": "mock", "store": "memory", "ai": "offline rules"}
    for bad in ("https://etalase.id", "Etalase.ID", "etalase.id/", "-etalase.id", "etalase id"):
        with pytest.raises(InsecureConfig, match="STORE_BASE_DOMAIN"):
            validate_settings(Settings(etalase_env="dev", store_base_domain=bad, _env_file=None), modes)
    for good in ("etalase.id", "localhost:8080", "shops.example.co.id"):
        validate_settings(Settings(etalase_env="dev", store_base_domain=good, _env_file=None), modes)


# ---------------------------------------------------------------- over HTTP
@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", DOMAIN)
    monkeypatch.setattr(app_ctx.settings, "public_base_url", "http://app.etalase.test")
    return TestClient(app)    # no lifespan: it would start the MCP session manager a second time in this process


def shop(c, wa, name):
    sel = c.post("/dev/sellers", json={"name": name, "whatsapp": wa}).json()
    c.post("/dev/whatsapp", json={"sender": wa, "text": "Hijab voal, hitam 5, navy 3, harga 85rb"})
    c.post("/dev/whatsapp", json={"sender": wa, "button_id": "publish"})
    return sel


def at(host):
    return {"host": host}


def test_a_new_shop_gets_an_address_and_serves_its_store_at_the_root(c):
    sel = shop(c, "6287710000041", "Sub Hijab Bandung")
    assert sel["slug"] == "sub-hijab-bandung"
    r = c.get("/", headers=at("sub-hijab-bandung.etalase.test"))
    assert r.status_code == 200 and "Sub Hijab Bandung" in r.text and "Voal" in r.text
    assert "script-src 'sha256-" in r.headers["content-security-policy"] and r.headers["content-security-policy"] != DASHBOARD_CSP
    assert '<meta property=og:url content="http://sub-hijab-bandung.etalase.test">' in r.text      # the canonical link is the shop's own
    assert "Sign in" not in r.text and "id=\"login\"" not in r.text


def test_the_platform_host_still_shows_the_dashboard_and_the_path_still_works(c):
    sel = shop(c, "6287710000042", "Sub Kopi Malang")
    assert "id=\"login\"" in c.get("/", headers=at("app.etalase.test")).text
    assert "Sub Kopi Malang" in c.get(f"/stores/{sel['id']}", headers=at("app.etalase.test")).text
    assert "id=\"login\"" in c.get("/", headers=at("etalase.test")).text           # the bare domain is not a shop


def test_an_unclaimed_address_is_a_404_and_never_the_dashboard(c):
    r = c.get("/", headers=at("nobody-here.etalase.test"))
    assert r.status_code == 404 and "no shop" in r.text and "login" not in r.text
    assert c.get("/stores/sel_x", headers=at("nobody-here.etalase.test")).status_code == 404      # any path, not only "/"


def test_product_pages_and_checkout_work_on_the_shop_host(c):
    sel = shop(c, "6287710000043", "Sub Batik Solo")
    host = "sub-batik-solo.etalase.test"
    store = c.get("/", headers=at(host)).text
    item = re.findall(r'href="(/stores/[^"]+/p/[^"?]+)"', store)[-1]
    page = c.get(item, headers=at(host))
    assert page.status_code == 200 and f"http://{host}/stores/{sel['id']}/p/" in page.text          # canonical on the subdomain
    buy = re.search(r'<a class=btn data-go href="([^"]+)">Buy now</a>', page.text).group(1)
    form = "qty=1&name=Wati&phone=085700998801&address=Jl.+Braga+7&city=Bandung&postal=40111"
    ok = c.post(buy, content=form, headers={"content-type": "application/x-www-form-urlencoded", "host": host,
                                            "origin": f"http://{host}"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"].startswith("/pay/")                    # same origin: accepted
    for origin in ("http://other-shop.etalase.test", "https://evil.example"):
        bad = c.post(buy, content=form, headers={"content-type": "application/x-www-form-urlencoded", "host": host, "origin": origin})
        assert bad.status_code == 403, origin                                                       # another shop is another origin


def test_the_seller_can_choose_the_address_and_sees_it_everywhere(c):
    wa = "6287710000044"
    sel = shop(c, wa, "Sub Teh Garut")
    c.post("/auth/otp", json={"whatsapp": wa})
    code = app_ctx.messenger.outbox[-1].text.split(": ")[1][:6]
    h = {"Authorization": "Bearer " + c.post("/auth/verify", json={"whatsapp": wa, "code": code}).json()["token"]}
    me = c.get("/api/me", headers=h).json()
    assert me["subdomains"] == {"domain": DOMAIN} and me["store_url"] == "http://sub-teh-garut.etalase.test"

    assert c.put("/api/me/slug", headers=h, json={"slug": "ab"}).status_code == 400
    assert c.put("/api/me/slug", headers=h, json={"slug": "www"}).status_code == 400
    shop(c, "6287710000045", "Another Shop")
    assert c.put("/api/me/slug", headers=h, json={"slug": "another-shop"}).status_code == 409
    assert c.put("/api/me/slug", headers=h, json={"slug": "teh-garut"}).json()["store_url"] == "http://teh-garut.etalase.test"
    assert c.get("/", headers=at("sub-teh-garut.etalase.test")).status_code == 404                  # the old address is gone
    assert "Sub Teh Garut" in c.get("/", headers=at("teh-garut.etalase.test")).text
    reply = c.post("/dev/whatsapp", json={"sender": wa, "text": "link toko"}).json()
    assert "http://teh-garut.etalase.test" in reply[-1]["text"]                                     # what the seller shares in chat
    assert c.put("/api/me/slug", json={"slug": "teh-garut"}).status_code == 401
    assert sel["id"]


def test_nothing_changes_without_a_base_domain(monkeypatch):
    monkeypatch.setattr(app_ctx.settings, "store_base_domain", None)
    plain = TestClient(app)
    sel = plain.post("/dev/sellers", json={"name": "Plain Shop", "whatsapp": "6287710000046"}).json()
    assert sel["slug"] is None
    assert "id=\"login\"" in plain.get("/", headers=at("plain-shop.etalase.test")).text            # no mapping
    plain.post("/auth/otp", json={"whatsapp": "6287710000046"})
    code = app_ctx.messenger.outbox[-1].text.split(": ")[1][:6]
    h = {"Authorization": "Bearer " + plain.post("/auth/verify", json={"whatsapp": "6287710000046", "code": code}).json()["token"]}
    me = plain.get("/api/me", headers=h).json()
    assert me["subdomains"] is None and f"/stores/{sel['id']}" in me["store_url"]
    assert plain.put("/api/me/slug", headers=h, json={"slug": "plain-shop"}).status_code == 404


def test_photos_load_from_the_shop_s_own_origin(c):
    """On a shop address the page and the media route share an origin, so the CSP's 'self' covers the photos even when the
    public base URL is plain http (the local try in SETUP.md); the meta tags still carry absolute URLs for outside readers."""
    sel = c.post("/dev/sellers", json={"name": "Sub Foto Jogja", "whatsapp": "6287710000044"}).json()
    app_ctx.catalog.ingest(sel["id"], ["Hijab voal, hitam 5, harga 85rb"], ["samples/voal-hijab.png"])
    app_ctx.catalog.publish(sel["id"])
    host = "sub-foto-jogja.etalase.test"
    page = c.get("/", headers=at(host)).text
    srcs = re.findall(r'<img src="([^"]+)"', page)
    assert srcs and all(src.startswith("/media/") for src in srcs)
    assert c.get(srcs[0], headers=at(host)).status_code == 200                       # the same host serves the file
    assert re.search(r'<meta property=og:image content="https?://[^"]+/media/', page)  # outside readers get an absolute URL
