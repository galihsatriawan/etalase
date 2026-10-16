# Security model

Who could attack Etalase, what they would go after, and which line of code (and which test) stops them.

::: tip What you will learn
- The threat model: attackers, targets and the control for each
- Fail-closed startup checks, `/dev` protection and how each webhook provider is authenticated
- Why a courier webhook can never release money on its own
- OTP sign-in limits, session tokens, rate limits, dashboard escaping, media path rules and tenant isolation
- PDP Law basics, data minimisation and the gaps still open before a pilot
:::

## Concepts first

Etalase moves money and holds buyers' addresses behind many public endpoints: provider webhooks, an MCP server, a Connector API, a dashboard. Three principles from **`prototype/etalase/security.py`** shape everything:

1. **Fail closed.** If a deployment is missing a secret that protects it, it refuses to start instead of running unprotected.
2. **Never trust a single signal for money.** A webhook saying "delivered" is a hint; the courier is asked directly before a payout.
3. **Identity comes from a verified channel.** The seller id comes from a verified WhatsApp number, a signed session token or a hashed API key, never from a request body or a model's output.

Status: every control on this page is <Badge type="tip" text="Built" /> and covered by tests unless marked otherwise.

## Threat model

| Attacker | Target | Attack | Control |
|---|---|---|---|
| Internet scanner | `/dev/*` simulation endpoints | Create sellers, mark orders paid | Off unless `ETALASE_ENV=dev`; `X-Dev-Token` on public URLs |
| Anyone | Payment webhook | Forge "paid" to get goods free | Xendit callback token; `WEBHOOK_TOKEN` for mock; atomic, idempotent status change |
| Seller or outsider | Shipping webhook, waybill | Forge "delivered" to release a payout | Courier is re-asked with `confirm_delivered` before money moves |
| Anyone | WhatsApp webhook | Impersonate a seller's message | `X-Hub-Signature-256` HMAC with the app secret |
| Attacker with a seller's number | Dashboard sign-in | Brute-force the 6-digit OTP | 5 tries per code, lockout after 10 failures per hour, per-IP limits |
| Attacker | Dashboard | Stored XSS through product titles | HTML escaping of all seller text; CSP |
| Malicious AI agent or buyer | Checkout | Hoard stock, spam orders, oversized input | Quantity cap, per-phone and per-IP limits, field validation |
| Malicious seller or prompt injection | Another seller's data | Act on someone else's orders or stock | `seller_id` from session state; ownership checks in services |
| Partner with a Connector key | Etalase's network | SSRF through the order webhook URL | Public `https` hosts only, re-checked at send time, no redirects |
| Anyone | Server files | Path traversal through `/media`, `/static` or the console messenger | File names only; `/static` serves two fixed font files by exact name; resolved path must stay inside allowed folders |
| Another website | The store link form | A hidden form that makes a visitor's browser place an order | `Origin` must equal `Host` on every form post; `null` and foreign origins get 403 |
| Seller or buyer text | Store, order and policy pages | Script injection through a title, note or address | Everything is escaped; the CSP allows one script pinned by hash and no inline handlers |
| Anyone who guesses an order id | Order page, return form | Read an order, or open a return for someone else | The id is a long random capability; the page shows no phone or address; a return also needs the buyer's phone, and a wrong phone answers exactly like an unknown id |
| A buyer, or an agent for one | Returns | Claim a refund after the window, twice, or against another seller's order | One guarded update per transition; the window is fixed at checkout; refund and payout are two claims on the same order, so only one can win; seller actions are scoped by `_seller_order` |
| A buyer | Etalase's network and the seller | Return evidence links that point at internal or malicious URLs | Stored, never fetched by Etalase; the seller sees only `https` links with `rel=noopener noreferrer nofollow`, labelled "open with care" |

## Fail-closed startup: `validate_settings`

`build_context` (`prototype/etalase/container.py`) calls `validate_settings` after wiring the adapters. On failure it raises `InsecureConfig` and the app never serves a request.

**`prototype/etalase/security.py`**

```python
def validate_settings(s: Settings, modes: dict) -> None:
    problems = []
    remote = not s.is_local
    if s.is_dev and remote:
        if not s.dev_token or len(s.dev_token) < MIN_SECRET_LEN:
            problems.append("DEV_TOKEN (16+ chars) is required when dev endpoints are reachable from the internet")
        # ... WEBHOOK_TOKEN 16+, SESSION_SECRET not the default
    if not s.is_dev:
        if s.session_secret == DEFAULT_SESSION_SECRET or len(s.session_secret) < 32:
            problems.append("SESSION_SECRET (32+ chars) is required in prod")
        # ... WEBHOOK_TOKEN 16+
        for part in ("payment", "shipping"):
            if modes.get(part) == "mock":
                problems.append(f"prod cannot run with the mock {part} adapter")
        if modes.get("messaging") != "whatsapp":
            problems.append("prod needs the WhatsApp Cloud API (WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID)")
    if s.whatsapp_token and not s.whatsapp_app_secret:
        problems.append("WHATSAPP_APP_SECRET is required to verify WhatsApp webhooks")
    # ... default WHATSAPP_VERIFY_TOKEN refused; Xendit key requires XENDIT_CALLBACK_TOKEN
    if problems:
        raise InsecureConfig("Refusing to start:\n- " + "\n- ".join(problems))
```

"Local" means `PUBLIC_BASE_URL` points at `localhost`, `127.0.0.1`, `0.0.0.0` or `testserver` (`Settings.is_local`).

| Deployment | Requires |
|---|---|
| Local dev | Nothing, so the demo runs with no setup |
| Public dev (e.g. a Cloud Run demo) | `DEV_TOKEN` and `WEBHOOK_TOKEN` (16+ chars), a non-default `SESSION_SECRET` |
| Prod | `SESSION_SECRET` 32+ chars, `WEBHOOK_TOKEN`, real payment, shipping and WhatsApp adapters |
| Any, with real WhatsApp | `WHATSAPP_APP_SECRET` and a changed `WHATSAPP_VERIFY_TOKEN` |
| Any, with real Xendit | `XENDIT_CALLBACK_TOKEN` |

## `/dev` protection

```python
def require_dev(request: Request, s: Settings) -> None:
    if not s.is_dev:
        raise HTTPException(404)
    if s.dev_token and not _eq(request.headers.get("x-dev-token"), s.dev_token):
        raise HTTPException(401, "X-Dev-Token required")
```

In prod every `/dev` route answers 404, as if it did not exist. In dev with a token set, the `X-Dev-Token` header must match.

```python
def _eq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())
```

`hmac.compare_digest` is a constant-time comparison, so response timing does not reveal how many characters were right. The `.encode()` matters too: for two `str` arguments, `compare_digest` raises `TypeError` if either contains a non-ASCII character, which an attacker can send in any header or query string. Comparing bytes turns that into a plain mismatch (401 or 403) instead of an unhandled exception (500). The WhatsApp verify-token check and the Meta signature check in `api/main.py` encode to bytes the same way.

## Webhook authentication per provider

| Webhook | Proof of origin | Where |
|---|---|---|
| `POST /webhooks/whatsapp` | `X-Hub-Signature-256: sha256=<HMAC of raw body with app secret>`; mandatory whenever the real Cloud API is on or an app secret is set. Console mode uses `WEBHOOK_TOKEN` | `api/main.py` |
| `GET /webhooks/whatsapp` (Meta's subscribe check) | Constant-time match of `hub.verify_token` | `api/main.py` |
| `POST /webhooks/telegram` | `X-Telegram-Bot-Api-Secret-Token` equals `TELEGRAM_WEBHOOK_SECRET` (set with `setWebhook`), constant-time compare; 503 if the secret is not configured. Without a bot (console mode) it needs `WEBHOOK_TOKEN` like the console WhatsApp webhook. Only private chats are read | `api/main.py` |
| `POST /webhooks/payment`, Xendit | `x-callback-token` header equals `XENDIT_CALLBACK_TOKEN` | `adapters/payment.py` |
| `POST /webhooks/payment`, mock | `WEBHOOK_TOKEN` | `api/main.py` |
| `POST /webhooks/shipping`, `POST /tasks/expire` | `WEBHOOK_TOKEN` as `?token=` or `X-Etalase-Webhook-Token` | `security.py` |
| `GET /tasks/disputes`, `POST /tasks/resolve-dispute` (these move money by hand) | `OPERATOR_TOKEN`, **header only** (`X-Etalase-Operator-Token`). A separate secret, because `WEBHOOK_TOKEN` sits in the Biteship webhook URL and so can end up in access logs. 503 on a public deployment until it is set | `security.py` |

Biteship and Cloud Scheduler cannot sign requests the way Meta does, so Etalase uses its own shared secret:

```python
def require_webhook_token(request: Request, s: Settings) -> None:
    """For webhooks without provider signatures. Open only on a local dev run with no token set."""
    if not s.webhook_token:
        if s.is_dev and s.is_local:
            return
        raise HTTPException(503, "WEBHOOK_TOKEN not configured")
    supplied = request.query_params.get("token") or request.headers.get("x-etalase-webhook-token")
    if not _eq(supplied, s.webhook_token):
        raise HTTPException(401, "invalid webhook token")
```

The mock QR string also hides the payment reference (`sha256(ref)[:16]` instead of `ref`): whoever knows a reference could otherwise confirm the payment through the mock webhook.

## Delivery verification before payouts

A shared token proves a request came from someone who knows the token, not that the parcel arrived. And a seller can type any waybill. So the webhook is only a hint:

**`prototype/etalase/checkout/service.py`**

```python
        if status == "delivered":
            # Money moves on delivery, so the webhook is only a hint: ask the courier first.
            if found.status != OrderStatus.SHIPPED or not self.shipping.confirm_delivered(found):
                log.warning("delivery for %s not confirmed by the courier; ignoring", found.id)
                return found
```

`BiteshipShipping.confirm_delivered` calls Biteship's tracking endpoint by waybill and courier and returns `True` only for status `delivered`; any HTTP error means "not confirmed". The payout itself is an atomic claim (`release_payout` sets `payout.status = "releasing"` inside `update_order`), so two webhooks cannot release money twice. Seller waybills must also match `[A-Z0-9-]{6,40}`. <Badge type="warning" text="Mocked in demo" /> `MockShipping.confirm_delivered` always returns `True`, which is why prod refuses the mock adapter.

## Dashboard sign-in: OTP and sessions

The seller dashboard has no passwords. The seller enters their number, Etalase sends a 6-digit code to the seller's chosen channel (WhatsApp by default, Telegram once linked), and a correct code returns a session token.

**`prototype/etalase/auth.py`**

```python
CODE_TTL = 5 * 60
SESSION_TTL = 7 * 24 * 3600
MAX_ATTEMPTS = 5              # wrong guesses per code
RESEND_COOLDOWN = 60          # seconds between codes for one number
MAX_CODES_PER_HOUR = 5        # codes sent per number per hour (caps WhatsApp cost)
MAX_FAILURES_PER_HOUR = 10    # wrong guesses per number per hour, across codes (blocks brute force)
```

- `POST /auth/otp` always answers `{"sent": true}`, whether or not the number is registered, locked or rate-limited, so attackers cannot enumerate sellers.
- Codes are stored as `sha256(secret + code)`, never in clear, and compared in constant time.
- A code dies after 5 wrong tries; 10 failures in an hour across codes lock the number.
- Per IP: 10 OTP requests and 30 verifications per hour.

With 10 guesses per hour against a million codes, a brute-force attacker succeeds about once in 100,000 hours of trying per number.

The session token is `<seller_id>.<expiry>.<signature>`, where the signature is the first 32 hex characters of HMAC-SHA256 with `SESSION_SECRET`. It is stateless: the server verifies the signature and expiry (7 days) without a database lookup. The dashboard keeps it in `sessionStorage` and sends it as `Authorization: Bearer ...`.

The same secret also signs the 10-minute Instagram OAuth `state` (`ig.<seller_id>.<expiry>`), which has the same three-part shape as a session token. To keep the two from ever being swapped, every signature includes its **purpose**:

```python
def _sig(self, payload: str, purpose: str) -> str:
    # The purpose is part of the MAC, so a signed OAuth state can never pass as a session token.
    return hmac.new(self.secret, f"{purpose}:{payload}".encode(), hashlib.sha256).hexdigest()[:32]


def _same(a: str, b: str) -> bool:
    """Constant-time compare that also accepts non-ASCII input (hmac.compare_digest raises on it for str)."""
    return hmac.compare_digest(a.encode(), b.encode())
```

`issue` and `seller_from_token` sign and check with `"session"`; `sign` and `unsign` use `"state"`. This is called **domain separation**: one key, several kinds of signed values, and a signature made for one kind is worthless for another. `test_session_tokens_and_oauth_state_are_not_interchangeable` checks both directions and also sends a non-ASCII token (`tök.123.äbc`) to prove it is rejected rather than crashing.

## Rate limits and `client_ip`

`RateLimiter` is a sliding window: a deque of timestamps per key, trimmed to the window on each call. Limits in the app:

| Bucket | Limit |
|---|---|
| `GET /agent/v1/search` | 120 per minute per IP |
| `GET /agent/v1/products/{product_id}/shipping-options` | 60 per minute per IP (each call can cost a courier-API request) |
| `POST /agent/v1/checkouts` | 20 per hour per IP |
| The same three buckets for the MCP tools `search_products` / `get_product` (search), `get_shipping_options`, `create_checkout` | shared with the REST routes: one count per IP, whichever door the agent uses; `get_checkout_status` has 240 per minute |
| Checkouts per buyer phone (REST and MCP) | 5 per hour |
| Gemini calls per seller (free-text turns plus catalog builds, only when Gemini is on) | 60 per hour (`SELLER_AI_CALLS_PER_HOUR`) |
| Units per checkout | 10 |
| Request body | 2 MB (413 above that) |

```python
def client_ip(request: Request) -> str:
    # Google's front end appends the address it saw to X-Forwarded-For, so the last entry is the
    # one a client cannot forge (earlier entries can be anything the client sent).
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return fwd[-1] if fwd else (request.client.host if request.client else "unknown")
```

::: warning Only correct behind Google's front end
On Cloud Run the last `X-Forwarded-For` entry is added by Google. Without such a proxy, a client can send its own header and pick any "IP", bypassing per-IP limits. MCP tools use the same limiter and the same `client_ip`; over the limit the agent receives `{"error": "rate_limited", ...}` as a normal tool result instead of an HTTP error (`gateway/mcp_server.py`, tests in `tests/test_gatekeeper.py`).
:::

## Dashboard: escaping and CSP

Product titles come from sellers, Gemini and Instagram captions, so they are untrusted. **`prototype/etalase/api/dashboard.html`** builds HTML with template strings, and every seller-provided value goes through `esc`:

```js
const esc = (v) => String(v ?? "").replace(/[&<>"'`]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;", "`": "&#96;"}[c]));
```

Numbers go through `Number(...)`. The `/` response also gets a Content-Security-Policy (`default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`), and every response gets `nosniff`, `no-referrer` and `X-Frame-Options: DENY` (the store pages override the referrer policy with `same-origin`, see below). The CSP allows `'unsafe-inline'` scripts because the dashboard is one inline file, so escaping is the real XSS defence and the CSP mainly blocks framing and data exfiltration to other hosts.

## The public store link and returns

The store, buy form, order page, return form and policy page (`gateway/storefront.py`, `gateway/ucp.py`, one look in `gateway/pages.py`) are the only pages a stranger can reach. Their controls:

- **Content-Security-Policy**: `default-src 'none'`; styles inline; fonts and `fetch` only from our own origin; images from our origin and `https`; `form-action 'self'`; `frame-ancestors 'none'`. Scripts: `script-src 'sha256-…'`, the hash of the one constant script in `pages.py`. Because the policy names a hash, any other script, including one a seller managed to inject, is refused by the browser. `test_the_only_script_on_a_buyer_page_is_ours_and_the_csp_pins_exactly_that_script` checks that every page carries exactly that script, no inline event handlers, nothing loaded from another site, and that the header's hash equals the hash of the page's script byte for byte.
- **Same-origin form posts**: `Origin` has to equal `Host`; `null` and foreign origins are refused with 403 (`test_a_form_post_from_another_site_is_refused`, `test_return_posts_from_another_site_are_refused`).
- **Referrer policy `same-origin`** on these pages. The API-wide `no-referrer` made Chromium send `Origin: null` on the form post, so our own check refused every real purchase. TestClient and httpx send no `Origin` at all, so no unit test could see it; it was found with a real browser and is now covered by `test_buyer_pages_use_a_same_origin_referrer_policy_so_real_browsers_send_their_origin` and an end-to-end step.
- **`Cache-Control: no-store`**, `noindex` on everything except the catalog page, 8 KB form bodies with every field clipped, and the same rate limits as the REST checkout.
- **The order id is a capability.** Knowing it lets you see the order page (no phone, no address on it); it never lets you act on the money. A return needs the buyer's phone as well, and a wrong phone gets the same "checkout not found" as an unknown id (`test_the_checkout_id_alone_is_not_enough`).

Returns add three more controls: the **window is a promise made at checkout** and stored on the order (`test_the_window_and_the_change_of_mind_promise_are_fixed_at_checkout`); **each transition is one guarded `update_order`**, so a second tap, a timer and a seller racing each other cannot refund twice or pay a refunded order (`test_a_second_tap_does_not_refund_twice`, `test_a_return_blocks_the_payout_and_asks_the_seller`); and **another seller cannot answer or confirm** someone else's return (`test_another_seller_cannot_answer_or_confirm`). Details are in [Checkout and orders](/architecture/checkout-and-orders).

## Media path restrictions

```python
@app.get("/media/{name}", include_in_schema=False)
def media(name: str):
    if "/" in name or "\\" in name or name.startswith(".") or not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(404)
    for root in (Path(ctx.media_dir).resolve(), SAMPLES_DIR):
        path = (root / name).resolve()
        if path.parent == root and path.is_file():
            return FileResponse(path)
    raise HTTPException(404)
```

Agents only ever see `/media/<file>` URLs, never server paths (`CatalogService.image_url`). The console messenger, where a simulated media id is a file path, refuses files outside the samples and media folders, so `/dev/whatsapp` cannot read `/etc/passwd`.

## Tenant isolation

| Layer | Mechanism |
|---|---|
| Seller agent | `seller_id` from ADK session state via `ToolContext`, never a tool argument |
| WhatsApp | Seller resolved from the verified sender number |
| Dashboard | Seller from the signed session token |
| Connector API | Seller from the hashed key's connection |
| Services | `_seller_order` and `set_stock` refuse other sellers' orders and products |
| Gemini prompts | Only the current seller's data |
| Buyer agents | Product views carry `content_note`: seller text is data, not instructions |

## Privacy: PDP Law basics

Indonesia's Personal Data Protection Law (UU 27/2022) governs how personal data is collected, used and kept. `docs/09-security-privacy-compliance.md` is planning notes, not legal advice, and marks several points **verify**. The approach is **data minimisation**: keep only what the job needs.

| Data | Where | Minimisation |
|---|---|---|
| Seller identity and bank details (KYC) | Payment provider | Etalase stores only the verification status and sub-account id |
| Seller WhatsApp number | Etalase | Needed for messaging and sign-in |
| Buyer name, phone, address | Order record | Needed for delivery; retention period to define |
| Product photos, voice notes | Local media folder today, Cloud Storage planned | Deleted on seller request |

Etalase's role for buyer data (acting for the seller and the transaction) and its contracts are still to be defined. Gemini runs on Vertex AI in an Asia-Pacific region; the docs ask to verify Google Cloud's current data-use terms.

## Control to test map

| Control | Test |
|---|---|
| Local dev starts with no secrets | `test_security.py::test_local_dev_starts_without_secrets` |
| Public dev needs tokens | `test_security.py::test_public_dev_deploy_requires_tokens` |
| Prod refuses mocks and default secret | `test_security.py::test_prod_refuses_mocks_and_default_secret` |
| Real providers need webhook secrets | `test_security.py::test_real_providers_need_their_webhook_secrets` |
| `X-Dev-Token` required | `test_api.py::test_dev_endpoints_need_the_dev_token_when_set` |
| `/dev` is 404 in prod | `test_api.py::test_dev_endpoints_do_not_exist_in_prod` |
| `WEBHOOK_TOKEN` on shipping, mock payment, tasks | `test_api.py::test_webhooks_without_provider_signatures_need_our_token` |
| Meta signature | `test_api.py::test_whatsapp_webhook_checks_meta_signature_when_secret_set` |
| Verify token | `test_api.py::test_whatsapp_webhook_verification` |
| Duplicate WhatsApp delivery ignored | `test_security.py::test_duplicate_whatsapp_delivery_is_ignored` |
| No payout without courier confirmation | `test_security.py::test_unconfirmed_delivery_does_not_release_money` |
| No double payout | `test_security.py::test_manual_payout_when_provider_is_not_automated` |
| Waybill format | `test_security.py::test_seller_waybill_must_look_like_one` |
| Mock QR hides reference | `test_security.py::test_mock_qr_does_not_leak_payment_reference` |
| OTP cooldown, max attempts, lockout | `test_otp_resend_cooldown`, `test_otp_code_dies_after_max_attempts`, `test_otp_lockout_across_codes` |
| Signed OAuth state | `test_security.py::test_signed_state_round_trip` |
| Session token and OAuth state not interchangeable; non-ASCII token rejected | `test_regressions.py::test_session_tokens_and_oauth_state_are_not_interchangeable` |
| Checkout input limits and per-buyer cap | `test_checkout_input_limits`, `test_checkouts_per_buyer_are_capped`, `test_api.py::test_rest_checkout_rejects_oversized_input` |
| CSP, `nosniff`, media traversal | `test_api.py::test_dashboard_headers_and_media_route` |
| Console media roots | `test_security.py::test_console_media_cannot_read_arbitrary_files` |
| Store link CSP pins exactly one script | `test_storefront.py::test_the_only_script_on_a_buyer_page_is_ours_and_the_csp_pins_exactly_that_script` |
| Font route serves a fixed list only | `test_storefront.py::test_the_display_font_is_served_from_a_fixed_list_and_cached` |
| Seller text cannot run as script or break out of JSON-LD | `test_storefront.py::test_seller_text_cannot_run_as_script_or_break_out_of_the_json_ld` |
| Cross-site form posts refused; real browsers send `Origin` | `test_a_form_post_from_another_site_is_refused`, `test_buyer_pages_use_a_same_origin_referrer_policy_so_real_browsers_send_their_origin` |
| Return needs the buyer's phone | `test_returns.py::test_the_checkout_id_alone_is_not_enough` |
| Refund and payout cannot both happen | `test_returns.py::test_a_second_tap_does_not_refund_twice`, `test_a_return_blocks_the_payout_and_asks_the_seller` |
| Another seller cannot answer a return | `test_returns.py::test_another_seller_cannot_answer_or_confirm` |
| Seller policy note is escaped and labelled as the seller's | `test_returns.py::test_return_policy_pages_are_public_and_escape_the_seller_note` |
| Telegram webhook secret; a bot token needs a valid secret | `test_telegram.py::test_the_webhook_needs_the_secret_when_a_real_bot_is_configured`, `test_a_bot_token_needs_a_valid_webhook_secret` |
| Telegram link codes: forged, altered, expired and moved chats | `test_telegram.py::test_bad_expired_or_forged_codes_link_nothing`, `test_a_linked_chat_cannot_be_moved_to_another_shop_by_a_code`, `test_a_stranger_gets_one_short_pointer_not_a_conversation` |
| The Telegram bot token never reaches a log or an error | `test_telegram.py::test_errors_carry_telegrams_reason_and_never_the_token`, `test_review_fixes.py::test_the_bot_token_is_not_written_to_the_log` (httpx's own INFO request log carried it) |
| A link code works once; a replaced chat is announced; strangers get a few replies | `test_review_fixes.py::test_a_link_code_works_once`, `test_a_code_cannot_be_used_twice_at_the_same_moment`, `test_replacing_the_linked_chat_tells_the_old_chat_and_the_whatsapp_number`, `test_a_stranger_cannot_make_the_bot_talk_endlessly` |
| Two shops cannot win one address; a released address is held back | `test_review_fixes.py::test_two_shops_racing_for_one_address_cannot_both_win`, `test_a_released_address_is_not_handed_to_a_stranger_at_once` |
| A tracking number belongs to one order | `test_review_fixes.py::test_another_shops_waybill_cannot_be_reused` |
| A stock error cannot swallow the paid alert or the refund | `test_review_fixes.py::test_a_stock_error_after_payment_still_tells_the_seller`, `test_a_stock_error_during_a_rejection_does_not_skip_the_refund` |
| A blocked Telegram chat cannot lock a seller out | `test_review_fixes.py::test_the_sign_in_code_falls_back_to_whatsapp_when_the_chat_is_blocked`, `test_order_alerts_fall_back_to_whatsapp_too`, `test_temporary_telegram_trouble_is_not_a_reason_to_switch_channel` |
| Host header: trailing dot, port, several labels never reach the dashboard | `test_review_fixes.py::test_a_name_nobody_claimed_is_never_the_dashboard` (7 spellings), `test_a_claimed_shop_answers_on_every_spelling_of_its_host` |
| Operator endpoints: own secret, header only | `test_review_fixes.py::test_operator_endpoints_need_the_operator_token_in_a_header`, `test_returns.py::test_rest_return_route_and_the_ops_endpoints` |
| Client IP behind a proxy; chunked uploads; malformed webhook bodies | `test_review_fixes.py::test_the_client_address_skips_the_trusted_proxy_entries`, `test_a_chunked_upload_over_the_limit_is_refused`, `test_malformed_telegram_updates_are_acknowledged`, `test_malformed_whatsapp_bodies_are_acknowledged` |
| Links typed by a buyer are not pushed into the seller's chat | `test_review_fixes.py::test_links_in_buyer_text_are_not_pushed_into_the_sellers_chat` |
| Shop addresses: validation, reserved names, Host mapping, unclaimed host is 404, other shops are other origins | `test_subdomains.py` (31 tests) |
| Agents get URLs, not paths | `test_security.py::test_agents_get_urls_not_file_paths` |
| Connector key required | `test_api.py::test_connector_requires_key_and_otp_rejects_wrong_code` |
| Webhook SSRF check | `test_security.py::test_connector_webhooks_only_to_public_https` |
| Tenant isolation | `test_checkout.py::test_tenant_isolation_on_orders`, `test_stock.py::test_seller_cannot_change_another_sellers_stock` |

**Not covered by a test yet:** the Xendit callback token check, session token expiry and tampering, non-ASCII values on the HTTP paths (`X-Dev-Token`, `?token=`, `hub.verify_token`), per-IP 429s, the OTP hourly code cap, dashboard escaping, the outgoing webhook signature, and the seller agent's tool scoping. [Testing](/ship/testing) shows how to add some of them.

## An independent review, and what changed

After the Telegram channel, shop addresses and returns were built, a reviewer who had not seen the code written ran probes against it (a fake Bot API, concurrent claims, hostile Host headers, a chunked upload) and reported 13 confirmed problems. Reading the list is a good way to learn what "secure enough to demo" still misses. Every row below has a regression test in `tests/test_review_fixes.py`; 37 of the first 49 fail on the code before the fixes.

| # | Finding | Severity | What changed |
|---|---|---|---|
| 1 | The Telegram bot token was written to the log: httpx logs each request URL at INFO, the Bot API puts the token in the URL, and FastMCP sets the root logger to INFO | High | `httpx` and `httpcore` loggers are held at WARNING |
| 2 | A link code could be replayed from another chat, and relinking was silent | Medium-high | Codes are single-use (an atomic record per code); a replaced chat is announced to the old chat and the WhatsApp number |
| 3 | Two shops could claim the same address in a race (check, then save) | Medium | The address is created atomically in a registry (`create_doc`); exactly one claim wins |
| 4 | Another seller's tracking number could be typed in, so that parcel's delivery started the wrong payout | Medium | A waybill already used by any order is refused |
| 5 | A stock error after payment skipped the paid alert; during a rejection it skipped the refund | Medium | Stock calls are guarded and flagged to an operator; the refund runs first |
| 6 | A seller whose Telegram chat became unreachable got no sign-in code and no alerts, and could not reach the dashboard to switch back | Medium-low | On a permanent Telegram refusal (blocked, chat not found) the message goes to the WhatsApp number |
| 7 | `shop.example.com.` (trailing dot) or `:443` in the Host header returned the dashboard | Low | Host is normalised; every host under the base domain that is not the platform host is a shop or 404 |
| 8 | The money endpoints accepted the shared webhook token in the query string, which also sits in the Biteship URL | Low-medium | A separate `OPERATOR_TOKEN`, accepted only in a header |
| 9 | Strangers got unlimited bot replies | Low | A few replies per hour per sender |
| 10 | A chunked upload had no size limit (the check read only `Content-Length`) | Low | Bytes are counted as they arrive |
| 11 | A released address could be taken by a stranger at once | Low | 30-day hold for the previous owner |
| 12 | Anonymous page views scanned all of a seller's orders | Low (cost) | Not fixed: listed under Known gaps |
| 13 | Malformed webhook bodies returned 500 | Low | Acknowledged and logged |

Also fixed because the reviewer flagged them as plausible: the client address behind a proxy (`TRUSTED_PROXY_HOPS`), links typed by a buyer being pushed into the seller's chat, and a shop name equal to the platform host's own label. Still open, and honestly so: payout stuck in `releasing` after a crash, whole-document `put_seller` overwrites, Firestore behaviour for odd ids (not testable here because the library is not installed), and everything the review could not reach because no real service was connected.

## Known gaps

From `docs/09` plus a code review:

- Rate limits, OTP counters, WhatsApp dedupe and ADK sessions are in memory per instance; move them to Memorystore or Firestore before running more than one instance.
- Instagram tokens should be encrypted with Cloud KMS (and exchanged for long-lived tokens).
- Uploaded media needs signed URLs, malware scanning and Cloud Storage.
- Xendit refunds and settlement are manual steps (`refund_pending`, `pending_manual`, an `ops.manual_action` ERROR log); nothing pages an operator yet. Biteship tracking payloads must be verified against the live API.
- Session tokens cannot be revoked before their 7-day expiry; sign-out only clears the browser.
- A Telegram link code is valid for 10 minutes and works once, but until it is used it is a bearer value: whoever uses it first links their chat. It is shown only inside a signed-in session. A replaced chat is announced to the old chat and to the WhatsApp number.
- A shop slug can imitate a brand: only a short reserved list is enforced and there is no abuse process. A released address is held for its previous owner for 30 days, but changing a slug still breaks links already shared.
- Waybill uniqueness is checked with a scan of orders at the moment the seller sends the number, not by a database constraint, so two sellers sending the same number in the same instant could both pass (Firestore needs a unique index).
- Behind a load balancer or Cloudflare, set `TRUSTED_PROXY_HOPS=1` or the rate limits count the proxy as one client. The right value depends on the proxy chain and was not verified against a real deployment.
- Buyer-written text in a seller message has links with a scheme or `www.` replaced; a bare domain can still be typed.
- Anonymous store, product and returns pages read all of a seller's orders to compute the track record (3 reads per store page). Harmless in memory, a cost multiplier on Firestore; cache or keep counters on the seller document.
- A payout stuck in `releasing` after a crash mid-release has no sweeper, and `put_seller` rewrites the whole seller document from a copy, so two simultaneous edits of different fields can lose one. Both need Firestore transactions or field-level updates.
- DNS-rebinding protection is turned off in `build_mcp` (see [Known gaps](/master/known-gaps)). `/mcp/` has no authentication; it is limited per IP and per buyer phone.
- No formal retention policy or DPIA (data protection impact assessment) yet.

## Check yourself

1. Someone deploys with `ETALASE_ENV=dev` to a public Cloud Run URL and forgets `DEV_TOKEN`. What happens?

::: details Answer
`validate_settings` sees a dev deployment that is not local and raises `InsecureConfig`; the app refuses to start.
:::

2. An attacker with the `WEBHOOK_TOKEN` posts "delivered" for a shipped order. Is money released?

::: details Answer
Only if the courier confirms. `shipment_update` calls `shipping.confirm_delivered`, which asks Biteship's tracking API by waybill. A fake waybill or forged status is ignored.
:::

3. Why does `POST /auth/otp` answer the same way for unknown numbers?

::: details Answer
So the endpoint cannot be used to discover which phone numbers are Etalase sellers, and so it does not reveal whether a number is locked or rate-limited.
:::

4. Why is the last `X-Forwarded-For` entry used, and when is that wrong?

::: details Answer
Google's front end appends the address it saw, so the last entry cannot be forged by the client on Cloud Run. Without that proxy, the client controls the whole header, and per-IP limits can be bypassed.
:::

5. Why does `OtpAuth._sig` take a `purpose` argument?

::: details Answer
The session token and the Instagram OAuth `state` are signed with the same secret and look alike (`a.b.signature`). Mixing the purpose into the MAC means a state signature is wrong for a session and vice versa, so a signed state from `/connect/instagram` can never be used as a dashboard login.
:::

6. A product title contains `<img src=x onerror=...>`. Why is the dashboard safe?

::: details Answer
`esc` replaces `<`, `>`, quotes and backticks with HTML entities before the title is inserted, so the browser shows it as text.
:::

## Next

[Testing](/ship/testing)
