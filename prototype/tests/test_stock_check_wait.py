"""A seller who does not answer a stock check: one reminder part way through, a notice when the check runs out, and an
honest answer for the buyer's agent and the store page. Nothing is charged at any point, because the payment code only
exists once the seller says the item is there."""

from datetime import timedelta

from etalase.gateway import storefront
from etalase.gateway.tools import GatewayTools, next_step
from etalase.models import OrderStatus, now

from .conftest import BUYER, variant


def stock_check(ctx, seller, product):
    navy = variant(product, "Navy")
    ctx.catalog.update_stock(seller.id, "Navy", 2)          # nearly sold out: confirm-first
    order = ctx.checkout.create_checkout(product.id, navy.id, 1, BUYER)
    assert order.status == OrderStatus.AWAITING_SELLER_CONFIRMATION
    return order


def sent_to_seller(ctx, seller):
    return [m for m in ctx.messenger.outbox if m.to == seller.address]


def test_the_seller_is_reminded_once_part_way_through_the_window(ctx, seller, product):
    order = stock_check(ctx, seller, product)
    before = len(sent_to_seller(ctx, seller))
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=10)) == []                    # too early
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=16)) == [order.id]
    reminder = sent_to_seller(ctx, seller)[before:]
    assert len(reminder) == 1 and "Reminder" in reminder[0].text and "Is it still available?" in reminder[0].text
    assert [b[0] for b in reminder[0].buttons] == [f"confirm:{order.id}", f"oos:{order.id}"]        # the same two buttons
    assert reminder[0].template.key == "stock_check"                                                  # no new template needed
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=20)) == []                    # once only
    assert ctx.store.get_order(order.id).stock_check_reminded


def test_no_reminder_once_the_seller_has_answered_or_the_check_is_about_to_end(ctx, seller, product):
    answered = stock_check(ctx, seller, product)
    ctx.checkout.seller_confirms_stock(seller.id, answered.id, available=True)
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=16)) == []
    late = stock_check(ctx, seller, product)
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=61)) == []                    # past the window: expiry's job
    assert not ctx.store.get_order(late.id).stock_check_reminded


def test_the_reminder_can_be_switched_off(ctx, seller, product):
    ctx.settings.seller_confirm_reminder_minutes = 0
    stock_check(ctx, seller, product)
    assert ctx.checkout.remind_stock_checks(now() + timedelta(minutes=30)) == []


def test_the_background_job_reminds_then_expires(ctx, seller, product):
    order = stock_check(ctx, seller, product)
    assert ctx.process_due(now() + timedelta(minutes=16))["stock_reminders"] == [order.id]
    done = ctx.process_due(now() + timedelta(minutes=61))
    assert done["expired"] == [order.id] and done["stock_reminders"] == []
    assert ctx.store.get_order(order.id).status == OrderStatus.EXPIRED


def test_the_seller_is_told_when_the_check_ran_out_of_time(ctx, seller, product):
    order = stock_check(ctx, seller, product)
    before = len(sent_to_seller(ctx, seller))
    ctx.checkout.expire_due(now() + timedelta(minutes=61))
    notice = sent_to_seller(ctx, seller)[before:]
    assert len(notice) == 1 and "no answer" in notice[0].text and "not charged" in notice[0].text
    assert notice[0].template.key == "stock_check_expired" and notice[0].buttons == []
    assert ctx.store.get_order(order.id).status == OrderStatus.EXPIRED


def test_no_expiry_notice_for_an_unpaid_checkout_that_was_not_a_stock_check_or_was_cancelled(ctx, seller, product):
    black = variant(product, "Black")
    plain = ctx.checkout.create_checkout(product.id, black.id, 1, BUYER)                 # instant: has a payment code
    before = len(sent_to_seller(ctx, seller))
    ctx.checkout.expire_due(now() + timedelta(minutes=16))
    assert ctx.store.get_order(plain.id).status == OrderStatus.EXPIRED
    cancelled = stock_check(ctx, seller, product)
    before = len(sent_to_seller(ctx, seller))
    ctx.checkout.cancel_unpaid(cancelled.id)                                            # the agent gave up: the seller is not told
    assert len(sent_to_seller(ctx, seller)) == before


def test_the_agent_sees_the_deadline_while_it_waits_and_the_reason_when_it_ends(ctx, seller, product):
    order = stock_check(ctx, seller, product)
    waiting = GatewayTools(ctx).get_checkout_status(order.id)
    assert order.expires_at.isoformat(timespec="minutes") in waiting["next_step"]
    assert "do not promise a quick answer" in waiting["next_step"] and "Nothing is charged" in waiting["next_step"]
    ctx.checkout.expire_due(now() + timedelta(minutes=61))
    ended = GatewayTools(ctx).get_checkout_status(order.id)
    assert ended["status"] == "expired" and ended["payment"] is None
    assert "did not confirm stock in time" in ended["next_step"] and "nothing was charged" in ended["next_step"]
    assert "expired unpaid" not in ended["next_step"]


def test_an_unpaid_instant_checkout_still_says_it_expired_unpaid(ctx, seller, product):
    black = variant(product, "Black")
    order = ctx.checkout.create_checkout(product.id, black.id, 1, BUYER)
    ctx.checkout.expire_due(now() + timedelta(minutes=16))
    assert next_step(ctx.store.get_order(order.id)).startswith("The checkout expired unpaid")


def test_the_store_page_says_the_seller_did_not_answer(ctx, seller, product):
    order = stock_check(ctx, seller, product)
    waiting = storefront.order_page(ctx, order.id).body.decode()
    assert "not charged before that" in waiting and "WIB" in waiting
    ctx.checkout.expire_due(now() + timedelta(minutes=61))
    page = storefront.order_page(ctx, order.id).body.decode()
    assert "The seller did not answer in time" in page and "no payment code was issued" in page
