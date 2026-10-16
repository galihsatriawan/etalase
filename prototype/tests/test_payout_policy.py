"""The published payout policy: it must say what the code does, with the code's own numbers, and promise nothing more."""

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app as real_app, ctx as real_ctx
from .test_storefront import form, order_id, post_form, shop


@pytest.fixture(scope="module")
def web():
    return TestClient(real_app)


def test_the_page_states_the_rule_with_the_numbers_the_code_runs_on(web):
    page = web.get("/policy/payouts")
    s = real_ctx.settings
    assert page.status_code == 200 and "How payouts are held" in page.text
    assert "3 days after delivery" in page.text and "7 days while a seller is new" in page.text       # 72 h and 168 h
    assert f"fewer than {s.new_seller_orders} completed orders" in page.text
    assert f"within {s.return_seller_response_hours} hours" in page.text and "silence counts as approval" in page.text
    assert "An Etalase operator, not the seller and not the buyer" in page.text
    assert "Etalase takes its commission at this moment only" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]


def test_the_page_never_uses_the_words_rekber_or_escrow_and_promises_no_speed_it_cannot_keep(web):
    text = web.get("/policy/payouts").text.lower()
    assert "rekber" not in text and "escrow" not in text and "rekening bersama" not in text
    assert "within 24 hours" not in text and "instantly" not in text and "guarantee" not in text


def test_the_page_is_honest_that_settlement_is_manual_until_it_is_automated(web, monkeypatch):
    assert "the order shows the payout as pending" not in web.get("/policy/payouts").text      # the mock provider settles at once
    monkeypatch.setattr(real_ctx.payment, "name", "xendit", raising=False)
    assert "operator sends each release by hand" in web.get("/policy/payouts").text


def test_an_appeal_is_offered_only_when_there_is_somewhere_to_send_it(web, monkeypatch):
    monkeypatch.setattr(real_ctx.settings, "support_contact", "")
    page = web.get("/policy/payouts").text
    assert "If you disagree with the decision" not in page and web.get("/policy/payouts.json").json()["appeal"] is None
    monkeypatch.setattr(real_ctx.settings, "support_contact", "help@<x>.example")
    page = web.get("/policy/payouts").text
    assert "If you disagree with the decision" in page and "help@&lt;x&gt;.example" in page        # escaped
    assert "within 7 days of the decision" in page and "A different team member reads the case again" in page
    assert web.get("/policy/payouts.json").json()["appeal"]["within_days"] == 7


def test_the_longest_wait_is_computed_not_typed(web, monkeypatch):
    s = real_ctx.settings
    expected = max(s.payout_hold_hours, s.new_seller_hold_hours) + s.return_seller_response_hours + s.return_lapse_days * 24
    assert web.get("/policy/payouts.json").json()["longest_wait_without_dispute_hours"] == expected == 168 + 48 + 168
    monkeypatch.setattr(s, "return_lapse_days", 3)
    assert web.get("/policy/payouts.json").json()["longest_wait_without_dispute_hours"] == 168 + 48 + 72


def test_a_deployment_without_a_hold_says_so(web, monkeypatch):
    monkeypatch.setattr(real_ctx.settings, "payout_hold_hours", 0)
    page = web.get("/policy/payouts")
    assert page.status_code == 200 and "released right after delivery" in page.text
    assert web.get("/policy/payouts.json").json()["hold_enabled"] is False


def test_the_order_page_the_return_policy_and_the_dashboard_link_to_it(web):
    s, p = shop(web)
    v = p.variants[0].id
    oid = order_id(post_form(web, f"/stores/{s['id']}/buy/{v}", form(v)))
    assert 'href="/policy/payouts"' in web.get(f"/pay/{oid}").text
    assert 'href="/policy/payouts"' in web.get(f"/stores/{s['id']}/returns").text
    assert 'href="/policy/payouts"' in web.get("/").text
