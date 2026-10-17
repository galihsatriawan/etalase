# 16. UCP: Etalase as a Universal Commerce Protocol business

**Status: built and tested against the official conformance suite, with stated gaps. Not certified (there is no certification), not integrated with AI Mode or the Gemini app.**

UCP is the open standard Google launched in January 2026 with Shopify, Etsy, Wayfair, Target and Walmart so that AI shopping surfaces can discover a business and run a checkout with it. Spec: <https://ucp.dev/2026-04-08/specification/overview/>. Code, schemas and tests: <https://github.com/Universal-Commerce-Protocol>.

## What Etalase implements (spec version 2026-04-08)

| Piece | Where | Notes |
|---|---|---|
| Discovery profile `GET /.well-known/ucp` | `prototype/etalase/gateway/ucp.py` | Validated against the official `BusinessSchema` (ucp-sdk 0.4.6) in `tests/test_ucp.py`. On a shop's own address (`rina.<domain>`, when `STORE_BASE_DOMAIN` is set) it is **that shop's** profile: its endpoint is the shop's address and its checkout only accepts that shop's items (`tests/test_shop_profiles.py`, validated against `BusinessSchema` too; the official conformance suite was run on the platform host only) |
| `dev.ucp.shopping.checkout` over REST | `/ucp/v1/checkout-sessions` (create, get, update, complete, cancel) | Every response is validated against the official `Checkout` model in the tests |
| `dev.ucp.shopping.fulfillment` extension | same | Domestic home delivery; courier quotes become fulfillment options |
| Hosted payment page (`continue_url`) | `GET /checkout/{id}` | QRIS code, refreshes itself; strict CSP, all text escaped |
| `refund_policy` link on every checkout | `links` array of the checkout, pointing to `/stores/{seller_id}/returns` | The seller's return policy as a page for people and `.json` for machines. Spec 2026-04-08 defines `refund_policy` as a well-known link type (`type` and `url` required, `title` optional). The same policy is also published as schema.org `MerchantReturnPolicy` in `/stores/{id}/products.jsonld`. See [doc 05](05-checkout-payment-payout.md#payout-hold-and-returns) |

Not implemented, and therefore not advertised: returns as a UCP capability (the return flow is our own MCP tool and REST endpoint; UCP gives agents only the policy link), payment handlers and AP2 mandates, cart, catalog, order, discount and buyer-consent capabilities, the MCP and A2A bindings of UCP (our own MCP server exists and is separate), message signing, multiple line items per session, pickup.

## How a UCP checkout maps onto Etalase

1. **Create** (`POST /checkout-sessions`). `item.id` is a variant id (what `/agent/v1` and the MCP tools return). One line item per session. The session is a draft; nothing is reserved yet.
2. **Update** (`PUT`). The platform sends buyer name and phone number, a shipping address, and later the chosen courier option. Etalase fetches courier quotes once per route change and lists them as fulfillment options. Missing or invalid data comes back as `messages` (code `missing` or `invalid`, with a `path`), status `incomplete`.
3. **Complete** (`POST .../complete`). Only when status is `ready_for_complete`. This is where Etalase creates its normal checkout: stock is reserved and the QRIS payment is created. The session becomes `requires_escalation` with a `requires_buyer_review` message and a `continue_url` to the hosted payment page, because QRIS must be paid by the buyer in a trusted UI and the spec tells platforms to hand the buyer over at that point. If the seller confirms stock first, the session is `complete_in_progress` until the payment code exists.
4. **Poll** (`GET`). `completed` plus `order` once the payment webhook has landed; `canceled` if the checkout expired or the seller rejected it.
5. **Cancel** (`POST .../cancel`). Gives the stock back for an unpaid checkout. A paid order answers `409`.

Design choices a reviewer may ask about:

- **Amounts are minor units per ISO 4217.** IDR has exponent 2, so Rp 85.000 is sent as `8500000`. Etalase itself stores whole rupiah; the conversion lives in one function (`to_minor`).
- **Closed checkouts answer `409`** (update, complete, or cancel of a paid order) as the suite expects. An unpaid checkout that is still being paid answers `200` with a recoverable message instead of changing.
- **Idempotency.** `Idempotency-Key` replays the stored answer for an identical request and returns `409` for a different body.
- **`UCP-Agent` is required but the platform profile is not fetched.** Fetching a URL taken from a request header is an SSRF risk, so capability negotiation is "return what we implement". An allow-listed fetch is needed before relying on negotiation.
- **Buyer name and phone number are required.** The courier and the seller contact the buyer by phone; a UCP buyer with only an email stays `incomplete` with a recoverable message.

## Test evidence (15 Oct 2026)

Three layers, each answering a different question:

1. **Our tests** (`tests/test_ucp.py`, 18 tests): profile and every checkout response validate against the official SDK models; end-to-end flow from empty session to paid order; idempotency, closed sessions, cancel, confirm-first sellers, escaping, rate limits.
2. **The official conformance suite against Etalase** (`python -m ucp_conformance.run`): **29 passed, 22 failed, 26 skipped** (77 tests in 17 files). Passing: discovery and version negotiation, idempotency (create, update, complete, cancel), totals, the fulfillment flow, validation (out of stock, unknown product, structured errors, update beyond stock), create/get/update/cancel in the lifecycle tests.
3. **The same suite against the official sample server** (`--target reference`): **76 passed, 0 failed, 1 skipped**. This shows the suite and our way of running it are sound, so the Etalase result is meaningful.

The 22 failures are listed one by one, with the reason, in `prototype/ucp_conformance/expected_failures.json`; the runner fails if a failure appears that is not listed. They fall into five groups:

| Group | Tests | Why they fail |
|---|---|---|
| Card or AP2 payment completes instantly | 8 (token binding, card credential, AP2, payment failure, complete checkout, complete is idempotent, and two lifecycle tests that need a completed checkout first) | The suite pays with a card instrument and expects `completed` in the same call. Etalase takes QRIS, which the buyer finishes on `continue_url`. The suite's buyer also has only an email, and it picks the flower-shop option id `std-ship` |
| Order capability | 6 | Not implemented, not advertised, but the suite does not skip these |
| Discount and buyer consent | 3 | Same: not advertised, not skipped |
| One line item only | 1 | The test sends several items |
| Sample-server hooks | 4 | `/testing/simulate-*` endpoints exist only in the reference server |

What this does not show: the checkout *completion* path is not exercised by the official suite for Etalase (it only passes through our own tests, with schema validation). A platform that insists on completing with a card instrument cannot use Etalase; a platform that follows `requires_escalation` and `continue_url` can. Whether Google's surfaces call an Indonesian business at all is a distribution question (see [known gaps](../learn/master/known-gaps.md)).

## Run it

```bash
cd prototype
pip install uv                                  # once
python -m ucp_conformance.run                   # Etalase: clones the official repos into .ucp-work/, starts the server, runs the suite
python -m ucp_conformance.run --target reference
python -m ucp_conformance.server --port 8190    # just the seeded server, to try requests by hand
```

```bash
curl -s localhost:8190/.well-known/ucp | python -m json.tool
curl -s -X POST localhost:8190/ucp/v1/checkout-sessions \
  -H 'UCP-Agent: profile="https://platform.example/profile"' -H 'Content-Type: application/json' \
  -d '{"line_items":[{"item":{"id":"item_1"},"quantity":2}]}' | python -m json.tool
```

## Open items

- Move to spec 2026-08-25 (the newest release) once the conformance suite and SDK for it are available to test against; the suite on `main` still targets 2026-04-08.
- Multiple line items per session (Etalase's `Order` already holds a list of items; checkout creation and shipping weight do not).
- Order capability and webhooks, so a platform can follow an order after payment.
- A hosted form on `continue_url` that collects a missing phone number, so `requires_buyer_input` can replace `incomplete` for buyers without one.
- State is in the shared store (Firestore in production), but the idempotency claim is read-then-write, not atomic; two identical requests in the same instant can both run.
