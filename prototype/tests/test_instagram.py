from pathlib import Path

from etalase.adapters.instagram import SampleInstagramSource, authorize_url
from etalase.models import SellMode

SAMPLE = str(Path(__file__).resolve().parents[1] / "samples" / "instagram_posts.json")


def test_import_turns_product_posts_into_drafts_and_skips_the_rest(ctx, seller):
    drafts = ctx.inbox.import_instagram(seller.id, SampleInstagramSource(SAMPLE))
    assert sorted(p.title for p in drafts) == ["Kemeja Linen Oversize", "Pashmina Silk"]
    pashmina = next(p for p in drafts if p.title == "Pashmina Silk")
    assert pashmina.variants[0].price == 120000
    assert pashmina.external_ref == "ig:18020000000000001"
    assert pashmina.images  # the post photo becomes the product photo
    last = ctx.messenger.outbox[-1]
    assert "1 non-product post(s) skipped" in last.text and last.buttons[0][0] == "publish"


def test_import_is_idempotent(ctx, seller):
    ctx.inbox.import_instagram(seller.id, SampleInstagramSource(SAMPLE))
    again = ctx.inbox.import_instagram(seller.id, SampleInstagramSource(SAMPLE))
    assert again == []
    assert "No new product posts" in ctx.messenger.outbox[-1].text
    assert len(ctx.store.list_products(seller_id=seller.id)) == 2


def test_imported_products_are_agent_visible_after_publish(ctx, seller):
    ctx.inbox.import_instagram(seller.id, SampleInstagramSource(SAMPLE))
    ctx.catalog.publish(seller.id)
    found = ctx.gateway.search_products("sage pashmina")
    assert found["count"] == 1
    v = found["results"][0]["variants"][0]
    assert v["options"]["color"] == "Sage" and v["sell_mode"] in (SellMode.INSTANT.value, SellMode.CONFIRM_FIRST.value)


def test_authorize_url_requests_basic_scope_only():
    url = authorize_url("123", "https://example.com/cb", "state")
    assert "scope=instagram_business_basic" in url and "response_type=code" in url
