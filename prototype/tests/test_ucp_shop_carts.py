"""A UCP cart (several line items in one session) on a shop's own address: only that shop's lines exist there, an update cannot
swap in another shop's line, the platform address still refuses a cart that mixes shops, and a shop that is not open holds
nothing. The fixtures and helpers are the ones in test_shop_profiles."""

from .conftest import variant
from .test_shop_profiles import DOMAIN, UCP, at, c, pending, two_shops  # noqa: F401  (fixtures)


def ucp_cart(host, *variants):
    body = {"line_items": [{"item": {"id": v.id}, "quantity": 1} for v in variants]}
    return body, {**UCP, **host}


def test_a_ucp_cart_on_a_shop_address_holds_only_that_shops_lines(c, two_shops):  # noqa: F811
    (a, pa), (b, pb) = two_shops
    black, navy, pink = variant(pa, "Black"), variant(pa, "Navy"), variant(pb, "Pink")

    body, headers = ucp_cart(at(a.slug), black, navy)
    made = c.post("/ucp/v1/checkout-sessions", json=body, headers=headers)
    assert made.status_code == 201 and [li["item"]["id"] for li in made.json()["line_items"]] == [black.id, navy.id]
    assert made.json()["links"][0]["url"].startswith(f"http://{a.slug}.{DOMAIN}/")        # links stay on the shop's address

    body, headers = ucp_cart(at(a.slug), black, pink)                                      # one line from the other shop
    refused = c.post("/ucp/v1/checkout-sessions", json=body, headers=headers).json()
    assert refused["ucp"]["status"] == "error" and refused["messages"][0]["code"] == "item_unavailable"

    body, headers = ucp_cart(at(), black, pink)                                            # the platform sees both, but a cart is one shop's
    mixed = c.post("/ucp/v1/checkout-sessions", json=body, headers=headers).json()
    assert mixed["messages"][0]["code"] == "unsupported" and "same shop" in mixed["messages"][0]["content"]

    swap = c.put(f"/ucp/v1/checkout-sessions/{made.json()['id']}", json={"line_items": body["line_items"][1:]},
                 headers={**UCP, **at(a.slug)})
    assert swap.status_code == 422 and swap.json()["messages"][0]["code"] == "item_unavailable"   # an update cannot swap in the other shop


def test_a_ucp_cart_cannot_hold_a_line_of_a_shop_that_is_not_open(c, two_shops, pending):  # noqa: F811
    (_, pa), _ = two_shops
    _, hidden = pending
    body, headers = ucp_cart(at(), variant(pa, "Black"), variant(hidden, "Grey"))
    refused = c.post("/ucp/v1/checkout-sessions", json=body, headers=headers).json()
    assert refused["messages"][0]["code"] == "item_unavailable"
