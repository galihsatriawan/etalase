# ADR 0011: the UCP Order capability, signed webhooks, and a sandbox-only test mode

**Status:** accepted, 18 Oct 2026. Not certified by anyone; no Google surface calls Etalase today ([doc 16](../16-ucp.md)).

## Context

UCP's Order capability lets a platform follow an order after the buyer has paid: the business serves `GET /orders/{id}` and **pushes a
signed webhook to the platform's declared `webhook_url` every time the order changes**. Etalase already had the checkout, the
fulfillment extension and buyer consent. Without Order, a platform that hands the buyer over to the QRIS page never learns that the
parcel shipped, was delivered, came back, or was refunded.

Three things make this more than a new endpoint:

- **Webhooks go to a URL that a stranger names.** The platform's profile says where. A business that posts to whatever a request
  header points at is a server-side request forgery (SSRF) hole.
- **A webhook has to be believable.** The platform must be able to tell a delivery from a forgery, so it is signed (RFC 9421) with a key
  published in our profile.
- **The official conformance suite cannot reach an order the way Etalase takes payment.** It creates every order by paying with a mock
  card token and drives shipping through `/testing/simulate-shipping/{id}` and `PUT /orders/{id}`. Etalase takes QRIS only
  ([ADR 0009](0009-qris-only-no-cod.md)), and a production business has no simulation hooks.

## Decision

1. **Advertise `dev.ucp.shopping.order` and serve `GET /ucp/v1/orders/{id}`.** The order is derived from the Etalase order plus a small
   stored record. It exists for the platform only **after payment**, and only for **the platform that created the checkout** (the
   profile in `UCP-Agent` must equal the one stored on the session). Anything else is a `404`, so an order id leaks nothing.
2. **Events and adjustments are append-only with stable ids** (`processing`, `shipped` with the waybill, `delivered`,
   `returned_to_sender`, `undeliverable`; `return`, `refund`, `cancellation`). A webhook is always the **full order**, never a delta.
   A fingerprint of the order decides whether anything changed since the last delivery, so a no-op sends nothing.
3. **Webhooks only go to allow-listed platforms** (`UCP_PLATFORMS`, a list of profile URLs, optionally with the hosts their webhook
   may use). The profile is fetched once per profile and cached. The delivery resolves the host **once**, refuses any address that is
   not public, connects to that address with the original `Host` and SNI, follows no redirect and caps the body. Outside the sandbox
   the profile and the webhook must be https.
4. **Signing:** RFC 9421 with ES256 (raw `r||s`) or Ed25519, covering `@method`, `@authority`, `@path`, `@query` if present,
   `content-digest`, `content-type`, `idempotency-key`, `ucp-agent`, `webhook-id` and `webhook-timestamp`; `Content-Digest` per RFC 9530;
   `kid` is the RFC 7638 thumbprint. The key is `UCP_SIGNING_KEY`. Outside `dev`, listing platforms without a key refuses to start;
   a throwaway key is generated in `dev` only, and without a configured key outside `dev` the profile publishes none and nothing is
   signed. The signing code follows the official sample server (Apache-2.0), with the structure of this repository.
5. **Delivery is a stored outbox**, not a thread that can be lost. A retry keeps the same `Webhook-Id`, timestamp and body. Back-off is
   2 s, 10 s, 1 min, 5 min, 30 min, 2 h, 6 h, 12 h. A `4xx` other than 408, 425 and 429 is permanent. Giving up raises an
   `ops.manual_action` alert. Webhooks of one order go out in order.
6. **A sandbox test mode (`UCP_TEST_MODE` plus `UCP_SIMULATION_SECRET`) exists so the official suite can exercise all of this.** It
   accepts the mock card token as a payment (and `fail_token` as a declined card), allows `localhost` platforms, and adds `PUT /orders/{id}`
   and `POST /testing/simulate-shipping/{id}` (behind the secret). **Startup refuses it outside `dev`, with a real payment or shipping
   adapter, or with a secret shorter than 16 characters.** Production behaviour is unchanged and is reported separately.
7. **The suite's gaps are filled by the harness, not by the product.** The suite sends no buyer (or only an e-mail address) and
   uses a US address, while Etalase needs a name and phone to ship and ships inside Indonesia. The harness passes an Indonesian address
   through the suite's own `--test_data_dir` and adds a name and a phone to create requests that lack them
   (`ucp_conformance/server.py`, `PlaceholderBuyer`). Nothing in `etalase/` knows about it.

## Results

Official suite, 77 tests: **production mode 31 passed, 26 failed, 20 skipped; test mode 54 passed, 3 failed, 20 skipped.** Every
failure is listed with its reason; the runner fails on one that is not. In production mode 23 of the 26 fail only because the suite
needs a card payment or a simulation hook that production does not have. In test mode the 3 are two discount tests (not implemented)
and one that sends a raw card number, which Etalase never accepts.

## Consequences

- **The test-mode numbers say the Order code follows the protocol; they do not say production accepts a card.** We report both columns
  and say which is which. Anyone can run `python -m ucp_conformance.run --mode test`.
- **We do not verify the signature of the platform's own requests.** `GET /orders/{id}` relies on the profile named in `UCP-Agent`,
  which a client can claim. This is stated in [doc 16](../16-ucp.md); verifying incoming signatures is the next step once platforms send them.
- **One more thing to configure for a real platform:** its profile in `UCP_PLATFORMS`, and a key that is kept (the `kid` changes with
  the key, so rotating means publishing both for a while).
- **Not done here, and related:** an order whose QRIS code expired has no "pay again" button, so the buyer starts a new checkout. Other
  payment methods (virtual account, e-wallet) need different payment details, expiry and refund paths; they are not decided here and
  would extend ADR 0009.
