# Setup: what you fill in, and where each value comes from

Everything is already wired. The only thing left is your keys. Put them in `prototype/.env`
(`make setup` creates it), run `make doctor`, and the doctor tells you which ones work.

```bash
cd prototype
make setup      # venv, dependencies, .env with a random SESSION_SECRET, offline checks, tests
$EDITOR .env    # fill in what you have (sections below)
make doctor     # tests every key you filled in against the real service
make demo       # the whole flow in the terminal
make run        # API on http://localhost:8080
```

Nothing is mandatory to run the demo: every missing key falls back to a mock (offline extraction rules,
mock QRIS, mock courier rates, console instead of WhatsApp). What each key buys you:

| Section | Fill in | Without it | Needed for |
|---|---|---|---|
| [1. Gemini](#1-gemini-the-one-that-matters-most) | `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) **or** a Google Cloud project | rule-based extraction (49% on our labelled set) | the Gen AI score: real extraction, the seller and buyer agents |
| [2. Deploy](#2-deploy-to-cloud-run) | a Google Cloud project with billing | runs on your laptop only | the public prototype link the submission asks for |
| [3. WhatsApp](#3-whatsapp-business-cloud-api) | 4 values + 6 message templates | orders print in the terminal | the "order lands in the seller's WhatsApp" demo |
| [4. Xendit](#4-xendit-qris) | 2 values | mock QRIS | real QR codes |
| [5. Biteship](#5-biteship-couriers) | 1 value + a small balance | mock rates | real courier rates |
| [3b. Telegram](#3b-telegram-optional-second-seller-channel) | 3 values (a bot from @BotFather) | WhatsApp is the only seller channel | the "same order, another channel" demo; alerts with no 24-hour window |
| [6. Instagram](#6-instagram-optional) | 2 values | bundled sample posts | importing a seller's real posts |
| [7. Shop addresses](#7-shop-addresses-optional) | 1 value + wildcard DNS and certificate | every shop link is `/stores/<id>` on one host | `rina.yourdomain` links, each with its own UCP profile |
| [7b. Sign-up in chat](#7b-sign-up-in-chat-optional) | 1 value (`SIGNUP_ENABLED=true`) | sellers exist only through `/dev/sellers` | a stranger builds a shop in the chat and picks its address first |

**Minimum for a convincing submission: sections 1 and 2.** Sections 3 to 6 make the demo more real; add them in order of the
time you have. Keep Xendit and Biteship in test mode for the competition: no real parcel moves and no customer pays. (Biteship still bills
test-mode rate and tracking calls to your balance, a few rupiah each; see section 5.)

> **What was checked and what was not.** The request and response shapes for Gemini, WhatsApp, Xendit and Biteship were
> compared with each provider's public documentation on 2026-10-15, and unit tests pin them (`tests/test_gemini.py`,
> `test_whatsapp.py`, `test_xendit.py`, `test_biteship.py`). Nothing here has talked to a live provider, because this
> sandbox has no keys. `make doctor` is the first real test. The dashboards (Meta, Xendit, Biteship) rename menus often and
> could not be opened, so menu names below are best knowledge; if one has moved, search the dashboard for the value's name.
> Xendit's current official reference for the QR endpoint could not be opened; its payload comes from Xendit's migration
> guide and community-published samples (details in section 4).

## 1. Gemini (the one that matters most)

Pick one.

**A. API key (fastest, 2 minutes)**
1. Open <https://aistudio.google.com/apikey> and create a key.
2. In `.env`: `GOOGLE_API_KEY=<the key>` (`GEMINI_API_KEY` works too; leave `GOOGLE_GENAI_USE_VERTEXAI=false`).

**B. Vertex AI (what the Cloud Run deploy uses when there is no key)**
1. A Google Cloud project with billing enabled, and `gcloud services enable aiplatform.googleapis.com`.
2. `gcloud auth application-default login`
3. In `.env`: `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=<project id>`, `GOOGLE_CLOUD_LOCATION=asia-southeast1`.
   If the doctor says the model is not found in that region, set `GOOGLE_CLOUD_LOCATION=global`.

**Model.** The default is `GEMINI_MODEL=gemini-3.8-flash`. Google now limits the older `gemini-2.5-*` models to people who
already used them, so a new key may get "not found" on 2.5. Alternatives if you want to trade quality for cost:
`gemini-3.5-flash-lite`. Gemini 3 models are called at their default temperature on purpose.

Requests are retried (4 attempts with backoff on 429/500/503/504). If Gemini still fails, catalog extraction falls back to the
offline rules and the seller agent answers "please try again in a minute" instead of staying silent.

Then `make doctor` makes one real call and shows what it extracted from a sample message, and
**`make eval` measures extraction accuracy on 43 labelled cases** and writes `prototype/evals/results/latest.md`.
Run it once with Gemini on and put *that* number on the slide, not the offline baseline.

## 2. Deploy to Cloud Run

Run it in **Cloud Shell** (already signed in) or after `gcloud auth login`:

```bash
cd prototype
PROJECT_ID=my-project make deploy-dry   # prints every step, changes nothing; read it once
PROJECT_ID=my-project make deploy       # the real thing, about 5 minutes
```

It enables the APIs, creates a least-privilege service account, creates the random `SESSION_SECRET`, `DEV_TOKEN`,
`WEBHOOK_TOKEN` and `OPERATOR_TOKEN` as secrets, copies **your `.env` keys into Secret Manager** (values never printed, never on a command line,
unchanged ones get no new version), deploys to `asia-southeast2` with `PUBLIC_BASE_URL` already set, and smoke-tests
`/healthz` and `/.well-known/ucp`. It is safe to re-run after you change `.env`.

If `GOOGLE_API_KEY` is in `.env` the service uses it; otherwise it uses Vertex AI through the service account.
To use Vertex AI on Cloud Run while keeping an AI Studio key in `.env` for local work, deploy with
`GEMINI_AUTH=vertex PROJECT_ID=my-project make deploy` (the key is then not copied to Secret Manager). `GEMINI_AUTH=apikey` forces the key.
At the end it prints every URL you need to paste into the dashboards below.

The default is `STORE_BACKEND=memory` and one instance, which is right for a demo. For a pilot, set
`STORE_BACKEND=firestore` (create a Native-mode database first) and raise `--max-instances`.

## 3. WhatsApp Business Cloud API

You need four values: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`
(required together; the doctor says so if one is missing), plus six message templates (step 7).

1. <https://developers.facebook.com/apps> → **Create app** → choose the use case **Connect with customers through WhatsApp**
   (older flow: type **Business**, then add the **WhatsApp** product). Link or create a Business portfolio when asked.
2. **WhatsApp → API Setup**: copy the **Phone number ID** (`WHATSAPP_PHONE_NUMBER_ID`). Meta gives you a free test number;
   you can add up to 5 recipient phones, each confirmed with a code Meta sends to it. Use your own phone as the "seller".
3. Token. The temporary token on that page lasts 24 hours: fine for a rehearsal, not for a recording day.
   For a permanent one: **Business Settings → Users → System users** → add a system user → **Add assets** → give it your app
   (*Manage app*) and your WhatsApp account (*Full control*) → **Generate token** with the permissions
   `whatsapp_business_messaging` and `whatsapp_business_management` (plus `business_management` if Meta lists it as required)
   → `WHATSAPP_TOKEN`.
4. **App settings → Basic → App secret** → `WHATSAPP_APP_SECRET`. The server rejects any webhook without a valid
   `X-Hub-Signature-256`, so this one is not optional.
5. `WHATSAPP_VERIFY_TOKEN`: any long random string you invent. It must not stay `etalase-dev-verify`.
6. **WhatsApp → Configuration → Webhook**: callback URL `https://<your service>/webhooks/whatsapp`, verify token = the string from
   step 5, then subscribe to the **messages** field. (After `make deploy` the URL is printed. Locally, use a tunnel such as
   `cloudflared tunnel --url http://localhost:8080` and set `PUBLIC_BASE_URL` to it.)
7. **Create the six templates** (stock check, new paid order, ship now, payout, return requested, return approved), category **Utility**, in the language of `SELLER_LOCALE` (`en` default). They are listed, with
   exact names, bodies, buttons and sample values, in [learn/ship/whatsapp-templates.md](learn/ship/whatsapp-templates.md);
   `cd prototype && python -m etalase.adapters.whatsapp_templates` prints the same list. Approval takes minutes to a day,
   so do this first.
8. `make doctor` calls Meta with the token and prints the phone number it found.

**The 24-hour rule, in practice.** WhatsApp lets a business send free-form messages and buttons only within 24 hours of the
person's last message to it. Everything that is a *reply* (catalog, stock updates, order buttons) works. Alerts Etalase starts
itself (stock check, new paid order, payout) try free-form first and, if Meta answers error 131047, are re-sent as the
templates from step 7. So: **before each demo, send "hi" from the seller phone to the Etalase WhatsApp number** (this also
opens the window for the sign-in code of the dashboard), and have the templates approved as the safety net.

**Not verified live:** the template message payload (it follows Meta's documented shape and is pinned by tests), inbound CSV
documents (Meta's list of supported document types is not explicit about `text/csv`; send a spreadsheet as text if it fails),
and AMR voice notes (Gemini does not accept AMR, so the seller is asked to describe the item in text).

## 3b. Telegram (optional second seller channel)

A seller can link a Telegram chat and receive orders, stock checks and payouts there instead of WhatsApp (no 24-hour window, no message fee). WhatsApp stays the sign-in identity.

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name and a username ending in `bot`. BotFather returns a token like `123456:ABC...` → `TELEGRAM_BOT_TOKEN`.
2. Make up a webhook secret: 16+ characters from `A-Z a-z 0-9 _ -` (for example `openssl rand -hex 16`) → `TELEGRAM_WEBHOOK_SECRET`. Telegram sends it back in the `X-Telegram-Bot-Api-Secret-Token` header on every call; the server refuses calls without it and refuses to start with a token but no valid secret.
3. Optional: `TELEGRAM_BOT_USERNAME` (without `@`). Left empty, the server asks Telegram once.
4. After the service is deployed (section 2), tell Telegram where to send updates:
   ```bash
   cd prototype && python -m etalase.adapters.telegram set-webhook https://<your service>/webhooks/telegram
   ```
5. `make doctor` checks the token, shows the bot name and warns if no webhook is set.
6. Try it: sign in to the dashboard → **Settings → Where Etalase messages you → Connect Telegram** → tap **Open Telegram** and **Start**. The page switches to "connected" within a few seconds; choose **Telegram** and the next order alert arrives there.

Locally, with no token, the same flow runs against the console outbox: `POST /api/me/telegram/link` gives `/start <code>`, and `POST /webhooks/telegram` with a Telegram-shaped update links the chat (`scripts/e2e_http.py` does exactly this). Not verified live: nothing here has talked to the real Bot API; the request shapes follow Telegram's documentation and tests pin them (`tests/test_telegram.py`).

## 4. Xendit (QRIS)

1. <https://dashboard.xendit.co> → switch to **Test mode** (no business activation needed) → **Settings → API Keys** → generate
   a secret key with **Write** on *Money-in products*, plus **Read** on *Money-out products* (that read permission is only
   used by the doctor's balance check; without it the doctor warns "key accepted but cannot read balance", which is fine)
   → `XENDIT_SECRET_KEY` (`xnd_development_...`).
2. **Settings → Developers → Callbacks**: copy the **Webhook verification token** → `XENDIT_CALLBACK_TOKEN`. Set the callback
   URL for **QR code paid & refunded** to `https://<your service>/webhooks/payment`. Xendit sends that token in the
   `x-callback-token` header; the server rejects anything else.
3. `make doctor` checks the key with a read-only balance call and warns if you pasted a live key while `ETALASE_ENV=dev`
   (a key and callback token from different modes is the usual cause of a 401).
4. To "pay" a test QR, use the payment simulator in the Xendit dashboard (QR code section, test mode) or Xendit's
   "simulate payment" API for the QR code id. The id is stored on the order.

**Limits and caveats**
- QRIS orders are capped at **Rp10.000.000**; larger checkouts are refused with a clear message instead of failing at Xendit.
- The adapter uses `POST /qr_codes` (`api-version: 2022-07-31`, `type: DYNAMIC`, `callback_url`, `expires_at`). Xendit's
  migration guide calls this endpoint **legacy** (the newer one is `/v3/payment_requests` with channel code `QRIS`) but gives no
  retirement date. Xendit's current reference page for it could not be opened here, so the exact field names are community-sourced
  and **unverified**. If `make doctor` or the first real checkout fails with a 400, the error body is logged: compare it with
  your dashboard's API reference and adjust `etalase/adapters/payment.py` (one small class; `tests/test_xendit.py` pins
  the shape we send).

## 5. Biteship (couriers)

1. <https://biteship.com> → create an account → open the dashboard's **Integrations / API** page → switch on **Testing mode**
   and create an API key. A test key starts with `biteship_test.` (a live one with `biteship_live.`) → `BITESHIP_API_KEY`.
2. **Top up a small balance.** Biteship's sandbox does not give free calls: checking rates and tracking are billed even with a test key,
   and so is the doctor's rate check. A few thousand rupiah is plenty.
3. Webhook (for delivery updates): in the dashboard's webhook settings register `https://<your service>/webhooks/shipping?token=<WEBHOOK_TOKEN>`
   as the **test** webhook URL (`make deploy` prints the URL with a `<WEBHOOK_TOKEN>` placeholder; the value is the Secret Manager secret `etalase-webhook-token`). Etalase reads the `order.status` events.
   Optionally add a header/signature in the dashboard if it offers one; Etalase relies on the `token` query parameter.
4. `make doctor` asks for a Jakarta to Bandung rate and prints the cheapest courier. Use a real Jakarta/Bandung postal code
   pair; the doctor sample already does.
5. To move a test parcel: in the dashboard open the test shipment and change its status (the dashboard has a status simulator
   for test orders), and the webhook arrives. Etalase re-checks delivery with Biteship before it pays the seller out.

**Requirements the adapter already handles, which matter when you set up the seller:** the origin must have a postal code
(it is sent as `origin_postal_code`), and every item needs a weight (Etalase sends the variant weight, at least 1 g).
Orders are booked through `POST /v1/orders` with your order id as `reference_id`.

## 6. Instagram (optional)

Needed only to import a seller's real posts. The bundled sample posts already show the flow.

1. In your Meta app add **Instagram → API setup with Instagram login**.
2. Copy the **Instagram app ID** → `INSTAGRAM_APP_ID` and the **Instagram app secret** → `INSTAGRAM_APP_SECRET`.
3. Add `https://<your service>/connect/instagram/callback` as a valid OAuth redirect URI, and add your Instagram
   professional account as an Instagram tester. Public use needs Meta App Review; development mode works for testers.

## 7. Shop addresses (optional)

Instead of `https://<service>/stores/sel_1a2b3c4d5e`, each shop can have `https://rina.yourdomain.com`. It is optional and off by default.

1. Own a domain, for example `etalase.id`, and set `STORE_BASE_DOMAIN=etalase.id` (a bare host: lowercase, no `https://`, no path; `localhost:8080` for a local try).
2. **DNS:** a wildcard record `*.etalase.id` pointing at the service, plus the record for the platform host itself (the dashboard stays on `PUBLIC_BASE_URL`, for example `app.etalase.id`).
3. **Certificate:** a wildcard certificate for `*.etalase.id`. Cloud Run's own domain mapping does not do wildcards, so put an HTTPS load balancer with a serverless NEG in front of the service
   and a Google-managed or uploaded wildcard certificate (a managed wildcard needs DNS authorization), or use Cloudflare in front (proxied wildcard record, "Full (strict)" TLS).
   **Tried for the judge sandbox on 18 Oct 2026 (Google route):** a global external Application Load Balancer with a serverless NEG, a Certificate Manager certificate for `domain` and `*.domain`
   (DNS authorization: one `_acme-challenge` CNAME at the DNS host), and a wildcard `A` record to the load balancer's IP. Cloud Run received the original `Host` header, so no code change was needed,
   and `PUBLIC_BASE_URL` stayed on the Cloud Run URL (that host is the platform host). A wildcard covers one label (`rina.domain`, not `a.rina.domain`). The HTTPS forwarding rule does not redirect plain `http://`: that needs a second rule on port 80 with a redirect-only URL map ([Google's pattern](https://docs.cloud.google.com/load-balancing/docs/https/setting-up-global-http-https-redirect)); without it, `http://` does not connect. The sandbox has that rule and redirects to https (checked 18 Oct 2026). The Cloudflare route was not tried: its free plan
   can proxy a wildcard record, but its Host-header override looked Enterprise-only in the docs we read, and a Cloud Run origin would probably need it (Cloud Run routes by `Host`; not tested).
4. `make doctor` resolves a random label under the domain and warns if the wildcard record is missing.
5. Shops get an address from their name when created; a seller changes it in the dashboard (**Settings → Your shop address**). The old address stops working, so the dashboard says so.
   The judge sandbox (`DEMO=1`) gives its demo shop `demo.<domain>` when `STORE_BASE_DOMAIN` is set (the name-based address if `demo` is taken).
6. Each address is also that shop's agent surface: `https://rina.etalase.id/.well-known/ucp` is Rina's UCP profile (its endpoint is her address), and `/ucp/v1`, `/agent/v1` and `/mcp` on it see
   only her products and orders. Check it with `curl -s https://rina.etalase.id/.well-known/ucp`. The platform host still serves every shop.

Local try: `STORE_BASE_DOMAIN=localhost:8080 PUBLIC_BASE_URL=http://localhost:8080 make run`, then open `http://<slug>.localhost:8080` (browsers resolve `*.localhost` to your machine).
A host nobody claimed answers 404 (also with a trailing dot or a port in the header), and the platform host keeps the dashboard at `/`.

**Behind a load balancer or Cloudflare**, also set `TRUSTED_PROXY_HOPS=1`. Etalase takes the client address from the end of `X-Forwarded-For`
(Cloud Run appends the address it saw); one more proxy in front means the last entry is the proxy, so every visitor would share one rate limit.
Log the header once on your deployment to confirm the right number; we could not verify it from the sandbox.

## 7b. Sign-up in chat (optional)

Off by default. With `SIGNUP_ENABLED=true`, a stranger who writes to your WhatsApp number is asked for a shop name, offered a unique address on `STORE_BASE_DOMAIN` (they can type another), and asked where they
ship from. The shop starts **invisible**: no store page, no search result, no UCP/MCP/REST answer until it is verified. Decision and limits: [ADR 0010](docs/adr/0010-chat-signup-address-first.md).

- With the **mock payment provider** (a local run, the sandbox) a finished sign-up is verified at once.
- With a **real provider** the shop waits for you. `GET /tasks/pending-sellers` lists them; after you have checked the seller's identity and bank account yourself, `POST /tasks/verify-seller` with
  `{"seller_id": "..."}` opens the shop (both need the `X-Etalase-Operator-Token` header). The provider's own identity check through a link is **not built**. Starting with `SIGNUP_ENABLED` and no `OPERATOR_TOKEN` is refused.
- An address not verified within `SIGNUP_HOLD_HOURS` (default 72) goes back to the pool; `SIGNUP_SHOPS_PER_HOUR` (default 20) caps new shops across the platform.
- Local try: `SIGNUP_ENABLED=true make run`, then `curl -X POST localhost:8080/dev/whatsapp -H 'content-type: application/json' -d '{"sender":"6281200001111","text":"hi"}'` and keep answering with the same sender.
- Not tested with live WhatsApp traffic. The "your shop is open" message needs an approved template when the seller's 24-hour window has closed, and none is defined.

## 8. Payout hold, disputes and parcel rechecks (optional, all have defaults)

These are settings, not keys. The defaults work; change them in `.env` (and `make deploy` copies them).

| Setting | Default | What to decide |
|---|---|---|
| `PAYOUT_HOLD_HOURS` / `NEW_SELLER_HOLD_HOURS` | 72 / 168 | How long a seller waits after the courier confirms delivery. `PAYOUT_HOLD_HOURS=0` pays at once and switches returns off |
| `SUPPORT_CONTACT` | empty | An address or number people can write to when they disagree with a dispute decision. **Set it only if someone will read it.** With it empty, `/policy/payouts` offers no appeal. `DISPUTE_APPEAL_DAYS` (default 7) is how long people have |
| `SHIPMENT_RECHECK_HOURS` | 6 | How often Etalase asks the courier again about a shipment it has not confirmed. Every recheck is a **billable Biteship tracking call**, so keep your balance topped up |
| `OPERATOR_TOKEN` | generated by `make deploy` | The header token for the endpoints that move money by hand (`/tasks/disputes`, `/tasks/resolve-dispute`), for the report at `/tasks/attribution`, and for verifying shops that signed up in chat (`/tasks/pending-sellers`, `/tasks/verify-seller`) |

Read `/policy/payouts` on your deployment before you give the store link to anyone: it is what buyers and sellers will be shown,
and it is generated from these settings. A parcel the courier loses is refunded to the buyer in full and the seller carries the loss;
decide whether you accept that rule before real money moves.

The store link sets one first-party cookie, `etl_src`, with a coarse source label (for example `chatgpt` or `instagram`) for 30 minutes.
Mention it in your privacy notice.

## Everything else, `make help`

| Command | What it does |
|---|---|
| `make setup` | one-time local setup (safe to repeat) |
| `make doctor` / `make doctor-offline` | check every configured service; the offline variant makes no network call |
| `make eval` / `make eval-baseline` | catalog extraction accuracy (offline rules; plus Gemini when a key is set) |
| `make test` | the unit-test suite, no network, no keys |
| `make demo` | the whole flow in the terminal on mock services |
| `make browser` | real-browser check of the buyer pages (smooth navigation, back button restoring typed values, option chips, sort and search, the QR code, no-JavaScript fallback); needs `pip install playwright` and Chromium, optional |
| `make judge` | the reviewers' sandbox at `/demo` (`DEMO_MODE`): one shared demo seller, a simulated seller phone, a simulate-payment button. `DEMO=1 PROJECT_ID=... make deploy` puts it on Cloud Run as its own service, taking only the Gemini key from `.env`. See [JUDGES.md](JUDGES.md) |
| `make e2e-judge`, `make browser-judge` | the reviewers' path through the sandbox, over real HTTP and in a real browser (the second needs Playwright) |
| `make e2e` | the same story over real HTTP: starts the app, then seller on WhatsApp, an agent over UCP and MCP, the public shop, the same seller on Telegram, payment, shipping, payout |
| `make run` | API on `http://localhost:8080` (reads `.env`) |
| `make deploy` / `make deploy-dry` | Cloud Run deploy using `.env` / print the plan only |
| `make ucp-conformance` | Google's official UCP conformance suite against Etalase (needs `git`, `uv`, network) |
| `make ucp-reference` | the same suite against the official sample server (proves the suite itself runs) |
| `make ucp-server` | Etalase on `:8190` with fixed demo items, to try UCP requests by hand |
| `make ucp-shopper` | a simulated AI shopping surface (a UCP client we wrote, not Google AI Mode) buys two products over UCP: discover, session, courier, `requires_escalation`, QRIS, `completed` |
| `make academy` | build the documentation site |

## Before you submit

- The repository must be **public** (the rules require it). It is private now: flip it in GitHub → Settings → Danger zone.
- Put real numbers on the deck: the Gemini eval result, not the offline baseline.
- Fill the deck placeholders: team slide, screenshots, links.
- Set `SUPPORT_CONTACT` if you want the payout policy page to offer an appeal (section 8), and decide who answers it.
- `make ucp-conformance` prints an honest scorecard; quote it as "runs the official suite", not as "certified".
