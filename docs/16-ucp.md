# 16. UCP: Etalase as a Universal Commerce Protocol business

**Status: built and tested against the official conformance suite (54 of 77 pass in the sandbox test mode, 31 in production mode, none unexplained), with stated gaps. Not certified (there is no certification), not integrated with AI Mode or the Gemini app.**

UCP is the open standard Google launched in January 2026 with Shopify, Etsy, Wayfair, Target and Walmart so that AI shopping surfaces can discover a business and run a checkout with it. Spec: <https://ucp.dev/2026-04-08/specification/overview/>. Code, schemas and tests: <https://github.com/Universal-Commerce-Protocol>.

## What Etalase implements (spec version 2026-04-08)

| Piece | Where | Notes |
|---|---|---|
| Discovery profile `GET /.well-known/ucp` | `prototype/etalase/gateway/ucp.py` | Validated against the official `BusinessSchema` (ucp-sdk 0.4.6) in `tests/test_ucp.py`. On a shop's own address (`rina.<domain>`, when `STORE_BASE_DOMAIN` is set) it is **that shop's** profile: its endpoint is the shop's address and its checkout only accepts that shop's items (`tests/test_shop_profiles.py`, validated against `BusinessSchema` too; the official conformance suite was run on the platform host only) |
| `dev.ucp.shopping.checkout` over REST | `/ucp/v1/checkout-sessions` (create, get, update, complete, cancel) | Every response is validated against the official `Checkout` model in the tests |
| `dev.ucp.shopping.fulfillment` extension | same | Domestic home delivery; courier quotes become fulfillment options |
| `dev.ucp.shopping.buyer_consent` extension | same, `buyer.consent` | `analytics`, `preferences`, `marketing` and `sale_of_data` are recorded on the session as the platform sends them (only true or false, only those four) and returned on every response. A request that sends consent replaces it, so it can be withdrawn; one that leaves it out keeps it. Etalase sends no marketing and sells no data, so nothing acts on it yet |
| `dev.ucp.shopping.order` capability | `gateway/ucp_order.py`, `gateway/ucp_signing.py`; [ADR 0011](adr/0011-ucp-order-capability.md) | `GET /ucp/v1/orders/{id}` and signed order webhooks to the platform's declared `webhook_url`. Only a platform on the allow-list (`UCP_PLATFORMS`) receives them. Each webhook is the full order, signed with RFC 9421 (ES256 or Ed25519) under a key published in the profile's `signing_keys`. Deliveries are queued, retried with a back-off, and given up on with an ops alert |
| Hosted payment page (`continue_url`) | `GET /checkout/{id}` | QRIS code, refreshes itself; strict CSP, all text escaped |
| `refund_policy` link on every checkout | `links` array of the checkout, pointing to `/stores/{seller_id}/returns` | The seller's return policy as a page for people and `.json` for machines. Spec 2026-04-08 defines `refund_policy` as a well-known link type (`type` and `url` required, `title` optional). The same policy is also published as schema.org `MerchantReturnPolicy` in `/stores/{id}/products.jsonld`. See [doc 05](05-checkout-payment-payout.md#payout-hold-and-returns) |

Not implemented, and therefore not advertised: returns as a UCP capability (the return flow is our own MCP tool and REST endpoint; UCP gives agents only the policy link), payment handlers and AP2 mandates, cart, catalog and discount capabilities, the MCP and A2A bindings of UCP (our own MCP server exists and is separate), verifying the signature of incoming requests, carts that mix more than one shop, pickup.

## How a UCP checkout maps onto Etalase

1. **Create** (`POST /checkout-sessions`). `item.id` is a variant id (what `/agent/v1` and the MCP tools return). A session holds up to 10 line items, all from one shop (a cart that mixes shops is refused with `unsupported`; the same variant sent twice is merged into one line). On a shop's own address only that shop's items exist. The session is a draft; nothing is reserved yet.
2. **Update** (`PUT`). The platform sends buyer name and phone number, a shipping address, and later the chosen courier option. Etalase fetches courier quotes once per route change and lists them as fulfillment options; for a cart the quote is for the whole parcel (summed weight and value), the same one the order is created with. Changing `line_items` replaces the cart and clears the chosen option. Missing or invalid data comes back as `messages` (code `missing` or `invalid`, with a `path`), status `incomplete`.
3. **Complete** (`POST .../complete`). Only when status is `ready_for_complete`. This is where Etalase creates its normal checkout: stock for every line is reserved together (all or nothing) and the QRIS payment is created. The session becomes `requires_escalation` with a `requires_buyer_review` message and a `continue_url` to the hosted payment page, because QRIS must be paid by the buyer in a trusted UI and the spec tells platforms to hand the buyer over at that point. If the seller confirms stock first, the session is `complete_in_progress` until the payment code exists.
4. **Poll** (`GET`). `completed` plus `order` once the payment webhook has landed; `canceled` if the checkout expired or the seller rejected it.
5. **Cancel** (`POST .../cancel`). Gives the stock back for an unpaid checkout. A paid order answers `409`.

Design choices a reviewer may ask about:

- **Amounts are minor units per ISO 4217.** IDR has exponent 2, so Rp 85.000 is sent as `8500000`. Etalase itself stores whole rupiah; the conversion lives in one function (`to_minor`).
- **Closed checkouts answer `409`** (update, complete, or cancel of a paid order) as the suite expects. An unpaid checkout that is still being paid answers `200` with a recoverable message instead of changing.
- **Idempotency.** `Idempotency-Key` replays the stored answer for an identical request and returns `409` for a different body.
- **`UCP-Agent` is required, and a platform profile is fetched only when the platform is on the allow-list.** Fetching a URL taken from a request header is an SSRF risk. The profile is fetched only to find the platform's `webhook_url`, only for a profile listed in `UCP_PLATFORMS`, only over https outside the sandbox, after resolving the host once and refusing any address that is not public, connecting to that address, and never following a redirect. Capability negotiation is still "return what we implement".
- **An order is visible to the platform that bought it, and only after payment.** `GET /orders/{id}` answers `404` to another platform and to anyone before the payment. Etalase does not yet verify the signature of the platform's own requests (platforms are not asked to sign them yet), so the profile named in `UCP-Agent` is the only check: this is a stated limitation, not a claim of authentication.
- **Buyer name and phone number are required.** The courier and the seller contact the buyer by phone; a UCP buyer with only an email stays `incomplete` with a recoverable message.

## The Order capability

After payment the platform can follow the order. Etalase publishes the order as the UCP `Order` entity, built from its own order and a small stored record: line items, the fulfillment expectation (courier, destination), and two append-only lists, `fulfillment.events` (`processing`, `shipped` with the waybill, `delivered`, `returned_to_sender`, `undeliverable`) and `adjustments` (`return`, `refund`, `cancellation`). Event and adjustment ids are stable, so a re-sent webhook says the same thing.

- **Webhooks.** When the order changes (paid, accepted, rejected, shipped, delivered, returned to sender, return requested, approved, refunded, closed), Etalase compares a fingerprint of the order with the last one it sent and queues a webhook only if it changed. The body is always the **full order**, never a delta. Headers: `Webhook-Id`, `Webhook-Timestamp`, `UCP-Agent` (the business profile URL), `Idempotency-Key`, `Content-Digest` (RFC 9530, sha-256), `Signature-Input` and `Signature` (RFC 9421).
- **Signing key.** `UCP_SIGNING_KEY` holds a PEM private key (EC P-256 or Ed25519). Its public half is in the profile's top-level `signing_keys`, under the RFC 7638 thumbprint as `kid`. Outside the sandbox a missing key refuses to start once platforms are listed; without a configured key the profile publishes none and nothing is signed.
- **Delivery.** Stored in the shared store, so a restart loses nothing. A retry sends the same `Webhook-Id`, timestamp and body. Retries after 2 s, 10 s, 1 min, 5 min, 30 min, 2 h, 6 h and 12 h; a `4xx` other than 408, 425 and 429 is permanent; when Etalase gives up it raises an `ops.manual_action` alert. Webhooks of one order are sent in order. A background loop in the API process sends them; `process_due` does the same on the scheduler.
- **Sandbox test mode (`UCP_TEST_MODE`).** The official suite creates every order by paying with a mock card token and drives shipping through `/testing/simulate-shipping/{id}` and `PUT /orders/{id}`. None of that exists in production. Test mode turns it on for the conformance run only: the mock card token pays, `localhost` platforms are allowed, and the two endpoints exist (the second behind `Simulation-Secret`). Startup refuses test mode outside `dev`, with a real payment or shipping adapter, or with a secret shorter than 16 characters.

## Test evidence (18 Oct 2026)

Three layers, each answering a different question:

1. **Our tests** (`tests/test_ucp.py`, 30 tests, `tests/test_ucp_shop_carts.py`, 2 tests, and `tests/test_ucp_order.py`, 45 tests that verify the signatures with an independent RFC 9421 verifier, the retry and give-up rules, the SSRF pinning and the startup checks): profile and every checkout response validate against the official SDK models; end-to-end flow from empty session to paid order, with one item and with a two-item cart; idempotency, closed sessions, cancel, confirm-first sellers, escaping, rate limits; carts: replace, merge, no mixing of shops, a short-stock line, the unit cap over the whole cart, and sessions stored before carts existed.
2. **The official conformance suite against Etalase** (`python -m ucp_conformance.run`), 77 tests in 17 files, run in two modes:
   - **Production mode: 31 passed, 26 failed, 20 skipped.** QRIS is the only way to pay, as in production. Passing: discovery and version negotiation, buyer consent, idempotency (create, update, complete, cancel), totals, the fulfillment flow (including a cart of several line items in one group), validation (out of stock, unknown product, structured errors, update beyond stock), create/get/update/cancel in the lifecycle tests.
   - **Test mode: 54 passed, 3 failed, 20 skipped** (`--mode test`, report in `ucp_conformance/results/latest_test_mode.md`). The suite's mock card token pays and its mock platform receives the signed webhooks, so the Order, webhook-structure, token-binding, AP2 and lifecycle tests can run. Of the 3 failures, 2 are discount tests (not implemented) and 1 sends a raw card number, which Etalase never accepts.
   - Test mode needs two things the suite does not provide for a business that ships inside Indonesia, both kept outside the product code: an Indonesian test address (`ucp_conformance/test_data/addresses.csv`, passed with the suite's own `--test_data_dir`), and a harness wrapper (`PlaceholderBuyer` in `ucp_conformance/server.py`) that adds a name and a phone number to a create request that lacks them, because the suite sends no buyer or only an e-mail address and Etalase needs a name and phone to ship.
   - **Signatures checked against the official verifier.** Once, by hand on 18 Oct 2026, a webhook signed by `Signer` (ES256 and Ed25519, with a query string and a non-default port in the URL) was accepted by `verify_request` from the official sample server's `ucp_signing.py`, and the same request with one byte added to the body was refused (`digest_mismatch`). This is not part of the automated tests, because the sample code is cloned only for `--target reference`.
3. **The same suite against the official sample server** (`--target reference`): **76 passed, 0 failed, 1 skipped**. This shows the suite and our way of running it are sound, so the Etalase result is meaningful.

The 26 production failures are listed one by one, with the reason, in `prototype/ucp_conformance/expected_failures.json` (the 3 test-mode failures in `expected_failures_test_mode.json`); the runner fails if a failure appears that is not listed. 23 of the 26 pass in test mode. They fall into four groups:

| Group | Tests | Why they fail in production mode |
|---|---|---|
| Card payment completes instantly | 8 (token binding, card credential, AP2, payment failure, complete checkout, complete is idempotent, and two lifecycle tests that need a completed checkout first) | The suite pays with a card instrument and expects `completed` in the same call. Etalase takes QRIS, which the buyer finishes on `continue_url`. 7 of the 8 pass in test mode; the card-credential test sends a raw card number and fails there too |
| Needs an order created by that card | 13 (4 order, 2 invalid-input, 6 webhook-structure, 1 webhook event stream) | The Order capability is advertised and works, but the suite can only obtain an order by paying with its mock card, and its webhook tests need `/testing/simulate-shipping`. All 13 pass in test mode |
| Discount | 2 | Not advertised, not skipped by the suite |
| Sample-server hooks | 3 | `/testing/simulate-*` endpoints exist only in the reference server and in test mode. All 3 pass in test mode |

The 20 skipped tests are skipped by the suite itself: they need fixtures we do not provide (discount codes, a known customer with stored addresses, a second destination) or features Etalase does not declare.

What this does not show: in production mode the checkout *completion* path is not exercised by the official suite for Etalase (it passes through our own tests, with schema validation, and in test mode through the mock card). Test mode is a sandbox configuration; the production flow never accepts a card token. A platform that insists on completing with a card instrument cannot use Etalase; a platform that follows `requires_escalation` and `continue_url` can. Whether Google's surfaces call an Indonesian business at all is a distribution question (see [known gaps](../learn/master/known-gaps.md)).

## Watch a surface buy from Etalase

`make ucp-shopper` (`prototype/scripts/ucp_shopper.py`) plays an AI shopping surface and prints every step: it reads `/.well-known/ucp`, opens a session with two products of one shop, sends the buyer and the address, picks a courier from the options Etalase returns, completes, receives `requires_escalation` with a `continue_url`, pays the QRIS code there and sees the session become `completed` with the order. It starts its own sandbox; against a running server it takes the address (`python scripts/ucp_shopper.py URL --demo-pay` on the deployed sandbox, or `--wait 120` to pay a real code yourself).

**What it is not:** it is a UCP client written for this repository, not Google AI Mode and not the Gemini app. Every call is a real call to the server; only the surface is simulated. Google's Merchant Center onboarding for UCP checkout is a US-only rollout by invitation (Australia and Canada expected in 2027, Indonesia not mentioned: [Google](https://support.google.com/merchants/answer/16992327?hl=en), checked 17 Oct 2026), so no Google surface can call Etalase today.

## Run it

```bash
cd prototype
pip install uv                                  # once
python -m ucp_conformance.run                   # Etalase: clones the official repos into .ucp-work/, starts the server, runs the suite
python -m ucp_conformance.run --mode test       # the same, with the sandbox test mode the Order tests need
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
- Carts that mix shops. A UCP session is one shop's cart because an order, its payout and its courier parcel belong to one seller; a platform that wants items from two shops opens two sessions.
- Verify the signature of the platform's own requests (RFC 9421) once platforms send them, so `GET /orders/{id}` can rely on more than the profile named in `UCP-Agent`.
- A "pay again" button for an order whose QRIS code has expired: today the buyer starts a new checkout. The fix is a new checkout from the old order's lines, with stock and price checked again.
- A hosted form on `continue_url` that collects a missing phone number, so `requires_buyer_input` can replace `incomplete` for buyers without one.
- State is in the shared store (Firestore in production), but the idempotency claim is read-then-write, not atomic; two identical requests in the same instant can both run.
