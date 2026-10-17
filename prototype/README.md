# Etalase prototype

Python 3.11+, FastAPI, Google ADK, MCP, deployable to Cloud Run.

## Quick start with make

```bash
make setup     # venv, dependencies, .env, offline checks, tests
make doctor    # after you fill .env: tests every key against the real service
make eval      # catalog-extraction accuracy (offline rules, plus Gemini when a key is set)
make e2e       # the whole story over real HTTP: starts the app, then seller, agent (UCP and MCP), payment, shipping, payout
make browser   # real-browser check of the buyer pages (needs playwright)
make judge     # the reviewers' sandbox at http://localhost:8080/demo (simulated chat, payment and courier; see ../JUDGES.md)
make e2e-judge       # the reviewers' path through the sandbox over real HTTP
make browser-judge   # the same path in a real browser, including the demo payment button (needs playwright)
make deploy    # PROJECT_ID=my-project make deploy: Cloud Run from your .env (make deploy-dry prints the plan only)
make help      # all targets, including the UCP conformance runner
```

Where every key comes from: [../SETUP.md](../SETUP.md).

## Quick start (offline, no credentials)

```bash
pip install -r requirements-dev.txt
python scripts/demo.py
pytest -q          # 627 tests, no network, no keys
```

The demo walks through ten steps: seller onboarding on WhatsApp, an optional Instagram import, an agent buying over MCP, payment,
acceptance, shipping, payout, stock updates by chat, a confirm-first order, a 50-agent race for the
last unit, dashboard sign-in and the Connector API.

## Run the server

```bash
uvicorn etalase.api.main:app --reload --port 8080
```

| URL | What |
|---|---|
| `/` | Seller dashboard (WhatsApp one-time-code sign-in; in dev mode the code is in `/dev/outbox`) |
| `/docs` | Interactive API docs (OpenAPI) |
| `/mcp/` | MCP server for AI agents (streamable HTTP) |
| `/.well-known/ucp` | UCP discovery profile (spec 2026-04-08) |
| `/ucp/v1/checkout-sessions` | UCP Checkout over REST (create, get, update, complete, cancel) |
| `/checkout/{id}` | Hosted QRIS payment page (the UCP `continue_url`) |
| `/stores/{seller_id}` | Public store link for buyers without an agent: catalog, product, buy form, order page (`/pay/{order_id}`), return policy (`/stores/{seller_id}/returns`). With `STORE_BASE_DOMAIN` set, a claimed shop also answers on `<shop>.<domain>` |
| `/policy/payouts`, `/policy/payouts.json` | The payout hold and return rules, generated from the settings the money code uses (for people and for agents) |
| `/agent/v1/*` | REST mirror of the agent tools |
| `/connector/v1/*` | Connector API for OMSs, plugins and website builders |
| `/connect/instagram` | Instagram Login for catalog import (needs `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`) |
| `/webhooks/whatsapp`, `/webhooks/telegram`, `/webhooks/payment`, `/webhooks/shipping` | Provider webhooks |
| `/tasks/expire` | Runs everything that is due: order expiry, courier rechecks, return timers, payout releases (Cloud Scheduler in production; needs `WEBHOOK_TOKEN`) |
| `/tasks/disputes`, `/tasks/resolve-dispute`, `/tasks/attribution` | Operator endpoints: open disputes, resolve one (refund or release), and the report of where orders came from. Header `X-Etalase-Operator-Token` |
| `/dev/*` | Simulation endpoints (only when `ETALASE_ENV=dev`; on a public URL they need the `X-Dev-Token` header) |
| `/media/<file>` | Product images for agents (file names only) |

Try it by hand:

```bash
curl -X POST localhost:8080/dev/sellers -H 'content-type: application/json' \
  -d '{"name":"Toko Rina","whatsapp":"6281234567890","city":"Jakarta","postal_code":"12950"}'
curl -X POST localhost:8080/dev/whatsapp -H 'content-type: application/json' \
  -d '{"sender":"6281234567890","text":"Voal hijab, black 5, navy 3, mocca 10, price 85k"}'
curl -X POST localhost:8080/dev/whatsapp -H 'content-type: application/json' \
  -d '{"sender":"6281234567890","button_id":"publish"}'
curl "localhost:8080/agent/v1/search?q=black%20voal%20hijab&max_price_idr=100000"
```

## Switching on real services

Copy `.env.example` to `.env` and fill in what you have. Each part switches independently.

| Variable(s) | Turns on |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` (or `GOOGLE_API_KEY`) | Gemini catalog extraction and the ADK seller agent |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | WhatsApp Business Cloud API |
| `XENDIT_SECRET_KEY`, `XENDIT_CALLBACK_TOKEN` | QRIS via Xendit (use test-mode keys) |
| `BITESHIP_API_KEY` | Real courier rates and bookings |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_USERNAME` | Telegram as the seller's channel (a seller links a chat with a one-time code) |
| `STORE_BASE_DOMAIN` | Shop addresses `<shop>.<domain>` (needs wildcard DNS and a wildcard certificate). Each address also serves that shop's own `/.well-known/ucp`, `/ucp/v1`, `/agent/v1` and `/mcp` |
| `SIGNUP_ENABLED`, `SIGNUP_SHOPS_PER_HOUR`, `SIGNUP_HOLD_HOURS` | Sign-up in chat (off by default). A new shop is invisible until verified; an address not verified in `SIGNUP_HOLD_HOURS` (72) goes back to the pool. With a real payment provider an operator verifies (`POST /tasks/verify-seller`, needs `OPERATOR_TOKEN`) |
| `PAYOUT_HOLD_HOURS`, `NEW_SELLER_HOLD_HOURS`, `NEW_SELLER_ORDERS`, `RETURN_SELLER_RESPONSE_HOURS`, `RETURN_LAPSE_DAYS` | The payout hold and returns (`PAYOUT_HOLD_HOURS=0` pays at once and switches returns off) |
| `SHIPMENT_RECHECK_HOURS` | How often unconfirmed shipments are asked about again (default 6; each recheck is a billable courier call) |
| `SUPPORT_CONTACT`, `DISPUTE_APPEAL_DAYS` | Where a seller or buyer writes to appeal a dispute decision, shown on `/policy/payouts`. With no contact set, no appeal is offered |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | Instagram catalog import via Instagram Login |
| `STORE_BACKEND=firestore` | Firestore with transactions |
| `SELLER_LOCALE=id` | Seller messages in Bahasa Indonesia |

`GET /healthz` shows which mode each part is in.

## Security defaults

- The app refuses to start on a non-localhost `PUBLIC_BASE_URL` without `DEV_TOKEN`, `WEBHOOK_TOKEN`
  and a real `SESSION_SECRET`; `ETALASE_ENV=prod` also refuses mock payment, shipping and messaging.
- Webhooks without provider signatures (shipping, mock payment, `/tasks/expire`) need
  `?token=<WEBHOOK_TOKEN>`; WhatsApp needs Meta's signature; Telegram needs its secret header; Xendit needs its callback token.
- The endpoints that move money by hand (`/tasks/disputes`, `/tasks/resolve-dispute`) need `OPERATOR_TOKEN` in the
  `X-Etalase-Operator-Token` header, a different secret from `WEBHOOK_TOKEN` (which sits in the Biteship URL), and answer 503 until it is set.
- Payouts are released only after the courier confirms delivery, never on a webhook alone. The same holds for a returned or lost
  parcel: the refund starts only after the courier's own tracking record agrees.
- Payment is QRIS only. A checkout asking for cash on delivery is refused (`etalase/payment_methods.py`, ADR 0009).
- The store link keeps one first-party cookie, `etl_src`, holding only a coarse source label for 30 minutes. Mention it in your privacy notice.
- Details: [docs/09](../docs/09-security-privacy-compliance.md#application-security).

## Demo buyer agent (needs Gemini)

```bash
export GOOGLE_API_KEY=...            # or Vertex AI settings
export ETALASE_MCP_URL=http://localhost:8080/mcp/
python -m etalase.agents.buyer_agent "Find a black voal hijab under Rp100k to Bandung 40135"
```

## Deploy to Cloud Run

```bash
PROJECT_ID=my-project ./scripts/deploy_cloud_run.sh
```

The script deploys to `asia-southeast2` (Jakarta) with Gemini on Vertex AI and secrets from Secret
Manager. See the comments in the script for one-time setup.

## Code map

```
etalase/
├── api/            FastAPI app, webhooks, dashboard
├── agents/         ADK seller agent, ADK buyer agent (MCP client)
├── gateway/        MCP server, agent tools, UCP server and profile, store link pages, shop-address middleware, payout policy page, schema.org data
├── catalog/        Gemini / offline extraction, normalisation, readiness, intents, search
├── checkout/       reservations, confirm-first, payment, shipping and parcel outcomes, payout hold, expiry (service.py), returns and disputes (returns.py)
├── seller/         inbox, notifications, channel choice (WhatsApp or Telegram), shop slugs, i18n (en / id)
├── connector/      Connector API service (push integrations, signed webhooks)
├── adapters/       payment (Xendit, mock), shipping (Biteship, mock), messaging (WhatsApp, Telegram, console), Instagram import
├── store/          MemoryStore, FirestoreStore
├── attribution.py  coarse order source labels (channel and referrer), never a URL or an IP
├── payment_methods.py  the accepted payment methods (QRIS only)
├── security.py     startup checks, rate limits, request limits
├── auth.py         one-time-code sign-in
├── doctor.py       `make doctor`: tests each configured key against the real service
├── events.py       event bus (Pub/Sub in production)
├── config.py       settings and country packs
└── container.py    wiring
```

## Notes

- Xendit refunds and sub-account settlement raise `ManualActionRequired` until the account setup is
  confirmed; the order is then marked `refund_pending` / `pending_manual` and an `ops.manual_action`
  event is published, instead of failing the webhook.
- `FirestoreStore` is meant to be run against the Firestore emulator first; automated tests use
  `MemoryStore`, which implements the same contract.
- The MCP session manager starts once per process, so tests share one app lifespan.
