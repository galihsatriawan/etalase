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
| Anyone | Server files | Path traversal through `/media` or the console messenger | File names only; resolved path must stay inside allowed folders |

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
| `POST /webhooks/payment`, Xendit | `x-callback-token` header equals `XENDIT_CALLBACK_TOKEN` | `adapters/payment.py` |
| `POST /webhooks/payment`, mock | `WEBHOOK_TOKEN` | `api/main.py` |
| `POST /webhooks/shipping`, `POST /tasks/expire` | `WEBHOOK_TOKEN` as `?token=` or `X-Etalase-Webhook-Token` | `security.py` |

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

The seller dashboard has no passwords. The seller enters their number, Etalase's WhatsApp number sends a 6-digit code, and a correct code returns a session token.

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
| Checkouts per buyer phone (REST and MCP) | 5 per hour |
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
On Cloud Run the last `X-Forwarded-For` entry is added by Google. Without such a proxy, a client can send its own header and pick any "IP", bypassing per-IP limits. MCP calls have no per-IP limit, only the per-phone checkout cap.
:::

## Dashboard: escaping and CSP

Product titles come from sellers, Gemini and Instagram captions, so they are untrusted. **`prototype/etalase/api/dashboard.html`** builds HTML with template strings, and every seller-provided value goes through `esc`:

```js
const esc = (v) => String(v ?? "").replace(/[&<>"'`]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;", "`": "&#96;"}[c]));
```

Numbers go through `Number(...)`. The `/` response also gets a Content-Security-Policy (`default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`), and every response gets `nosniff`, `no-referrer` and `X-Frame-Options: DENY`. The CSP allows `'unsafe-inline'` scripts because the dashboard is one inline file, so escaping is the real XSS defence and the CSP mainly blocks framing and data exfiltration to other hosts.

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
| Agents get URLs, not paths | `test_security.py::test_agents_get_urls_not_file_paths` |
| Connector key required | `test_api.py::test_connector_requires_key_and_otp_rejects_wrong_code` |
| Webhook SSRF check | `test_security.py::test_connector_webhooks_only_to_public_https` |
| Tenant isolation | `test_checkout.py::test_tenant_isolation_on_orders`, `test_stock.py::test_seller_cannot_change_another_sellers_stock` |

**Not covered by a test yet:** the Xendit callback token check, session token expiry and tampering, non-ASCII values on the HTTP paths (`X-Dev-Token`, `?token=`, `hub.verify_token`), per-IP 429s, the 2 MB body limit, the OTP hourly code cap, dashboard escaping, the outgoing webhook signature, and the seller agent's tool scoping. [Testing](/ship/testing) shows how to add some of them.

## Known gaps

From `docs/09` plus a code review:

- Rate limits, OTP counters, WhatsApp dedupe and ADK sessions are in memory per instance; move them to Memorystore or Firestore before running more than one instance.
- Instagram tokens should be encrypted with Cloud KMS (and exchanged for long-lived tokens).
- Uploaded media needs signed URLs, malware scanning and Cloud Storage.
- Xendit refunds and settlement are manual steps (`refund_pending`, `pending_manual`, an `ops.manual_action` ERROR log); nothing pages an operator yet. Biteship tracking payloads must be verified against the live API.
- Session tokens cannot be revoked before their 7-day expiry; sign-out only clears the browser.
- `/mcp/` has no per-IP limit and DNS-rebinding protection is turned off in `build_mcp` (see [Known gaps](/master/known-gaps)).
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
