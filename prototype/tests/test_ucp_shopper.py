"""scripts/ucp_shopper.py: the simulated AI shopping surface. It must walk the UCP journey the way the protocol describes it (not a
shortcut of ours), buy from one shop only, stop at the payment page when nobody pays, and say plainly that it is not Google."""

import importlib.util
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from etalase.api.main import app, ctx

from .conftest import make_product, make_seller

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ucp_shopper.py"
spec = importlib.util.spec_from_file_location("ucp_shopper", SCRIPT)
shopper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shopper)

_phones = count(6285720000000)          # Etalase limits checkouts per phone number per hour, and every test is a new buyer
_shops = count(1)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def buyer():
    return {**shopper.BUYER, "phone_number": str(next(_phones))}


def open_shop(name_word):
    """A shop with two products whose names no other test uses, so a search finds exactly these."""
    n = next(_shops)
    seller = make_seller(ctx, name=f"Shopper Shop {n}", wa=f"628120{n:07d}")
    hijab = make_product(ctx, seller, f"{name_word} hijab, black 5, navy 3, price 85k")
    shirt = make_product(ctx, seller, f"{name_word} shirt, black 4, white 2, price 120k")
    return seller, hijab, shirt


def quiet(*_):
    pass


def test_the_shopper_walks_the_ucp_journey_and_stops_at_the_payment_page(client):
    seller, hijab, shirt = open_shop("Quillon")
    result = shopper.shop(client, ("quillon hijab", "quillon shirt"), buyer=buyer(), say=quiet)
    assert [n for n, _, _ in result["steps"]] == [1, 2, 3, 4, 5, 6]                      # no "buyer pays": nobody paid
    assert result["status"] == "requires_escalation" and result["continue_url"].endswith(f"/checkout/{result['session_id']}")
    assert result["total"] == (85000 + 120000 + 12000) * 100                              # two products, cheapest courier, minor units
    assert len(result["couriers"]) == 3
    assert sorted(i["product_id"] for i in result["items"]) == sorted([hijab.id, shirt.id])
    order = ctx.store.list_orders(seller.id)[0]
    assert len(order.items) == 2 and order.status.value == "awaiting_payment"          # the stock of both lines is held, nothing is paid


def test_a_payment_makes_the_session_completed_with_the_order(client):
    seller, *_ = open_shop("Brindle")

    def pay(http, page):
        assert "QRIS" in page                                                          # the buyer pays on the page continue_url points to
        ctx.checkout.payment_succeeded(ctx.store.list_orders(seller.id)[0].payment.reference)

    result = shopper.shop(client, ("brindle hijab", "brindle shirt"), buyer=buyer(), say=quiet, pay=pay)
    assert result["status"] == "completed" and result["order"] == ctx.store.list_orders(seller.id)[0].id
    assert [n for n, _, _ in result["steps"]][-1] == 7


def test_a_payment_that_never_comes_is_a_failure_when_the_caller_expected_one(client):
    open_shop("Cinder")
    with pytest.raises(shopper.Failed, match="not completed"):
        shopper.shop(client, ("cinder hijab", "cinder shirt"), buyer=buyer(), say=quiet, pay=lambda http, page: None)


def test_both_products_come_from_one_shop_even_when_another_shop_sells_the_same_thing(client):
    first, *_ = open_shop("Dapple")
    second, *_ = open_shop("Dapple")                                                   # the same product names, another seller
    items = shopper.find_items(client, ["dapple hijab", "dapple shirt"])
    assert len({i["shop"] for i in items}) == 1 and len(items) == 2
    result = shopper.shop(client, ("dapple hijab", "dapple shirt"), buyer=buyer(), say=quiet)
    assert len(ctx.store.list_orders(first.id)) + len(ctx.store.list_orders(second.id)) == 1     # one order, from one shop


def test_a_variant_that_needs_the_sellers_answer_is_not_picked(client):
    seller, hijab, shirt = open_shop("Ember")
    for v in shirt.variants:
        ctx.store.set_stock(seller.id, shirt.id, v.id, None, "chat")                   # stock unknown: confirm-first
    with pytest.raises(shopper.Failed, match="found 1 in-stock"):
        shopper.find_items(client, ["ember hijab", "ember shirt"])


def test_nothing_to_buy_is_said_plainly(client):
    with pytest.raises(shopper.Failed, match="found 0 in-stock"):
        shopper.find_items(client, ["zzz nothing sells this", "qqq"])


def test_the_demo_button_needs_a_sandbox_and_says_so(client):
    with pytest.raises(shopper.Failed, match="not a sandbox"):
        shopper.demo_button(client, "<html>a payment page without the demo button</html>")


def test_it_says_it_is_not_google_and_amounts_are_minor_units():
    assert "not Google AI Mode" in shopper.BANNER and "no Google surface calls Etalase today" in shopper.BANNER
    assert shopper.idr(30700000) == "Rp 307.000" and shopper.rupiah(85000) == "Rp 85.000"


def test_an_unreachable_server_is_a_message_not_a_traceback(capsys):
    assert shopper.main(["http://127.0.0.1:9"]) == 1
    assert "FAILED: could not talk to the server" in capsys.readouterr().out
