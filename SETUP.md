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
| [1. Gemini](#1-gemini-the-one-that-matters-most) | `GOOGLE_API_KEY` **or** a Google Cloud project | rule-based extraction (49% on our labelled set) | the Gen AI score: real extraction, the seller and buyer agents |
| [2. Deploy](#2-deploy-to-cloud-run) | a Google Cloud project with billing | runs on your laptop only | the public prototype link the submission asks for |
| [3. WhatsApp](#3-whatsapp-business-cloud-api) | 4 values | orders print in the terminal | the "order lands in the seller's WhatsApp" demo |
| [4. Xendit](#4-xendit-qris) | 2 values | mock QRIS | real QR codes |
| [5. Biteship](#5-biteship-courier-rates) | 1 value | mock rates | real courier rates |
| [6. Instagram](#6-instagram-optional) | 2 values | bundled sample posts | importing a seller's real posts |

**Minimum for a convincing submission: sections 1 and 2.** Sections 3 to 6 make the demo more real; add them in order of the
time you have. Keep Xendit and Biteship in test mode for the competition: nobody needs real money to move.

> The provider dashboards (Meta, Xendit, Biteship) rename menus often and this sandbox cannot open them, so the menu names
> below are my best knowledge of the current layout. If one has moved, search the dashboard for the value's name; `make doctor` is the real test.

## 1. Gemini (the one that matters most)

Pick one.

**A. API key (fastest, 2 minutes)**
1. Open <https://aistudio.google.com/apikey> and create a key.
2. In `.env`: `GOOGLE_API_KEY=<the key>` (leave `GOOGLE_GENAI_USE_VERTEXAI=false`).

**B. Vertex AI (what the Cloud Run deploy uses when there is no key)**
1. A Google Cloud project with billing enabled, and `gcloud services enable aiplatform.googleapis.com`.
2. `gcloud auth application-default login`
3. In `.env`: `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=<project id>`, `GOOGLE_CLOUD_LOCATION=asia-southeast1`.

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

It enables the APIs, creates a least-privilege service account, creates the random `SESSION_SECRET`, `DEV_TOKEN`
and `WEBHOOK_TOKEN` as secrets, copies **your `.env` keys into Secret Manager** (values never printed, never on a command line,
unchanged ones get no new version), deploys to `asia-southeast2` with `PUBLIC_BASE_URL` already set, and smoke-tests
`/healthz` and `/.well-known/ucp`. It is safe to re-run after you change `.env`.

If `GOOGLE_API_KEY` is in `.env` the service uses it; otherwise it uses Vertex AI through the service account.
At the end it prints every URL you need to paste into the dashboards below.

The default is `STORE_BACKEND=memory` and one instance, which is right for a demo. For a pilot, set
`STORE_BACKEND=firestore` (create a Native-mode database first) and raise `--max-instances`.

## 3. WhatsApp Business Cloud API

You need four values: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`.
They are required together (the doctor says so if one is missing).

1. <https://developers.facebook.com/apps> → **Create app** → type **Business** → add the **WhatsApp** product.
2. **WhatsApp → API Setup**: copy the **Phone number ID** (`WHATSAPP_PHONE_NUMBER_ID`) and the **temporary access token**
   (`WHATSAPP_TOKEN`; it lasts 24 hours, which is fine for a rehearsal). Add the phone you will test with as a recipient.
   For anything longer than a day, create a **System User** in Business Settings and generate a permanent token with
   `whatsapp_business_messaging` and `whatsapp_business_management`.
3. **App settings → Basic → App secret** → `WHATSAPP_APP_SECRET`. The server rejects any webhook without a valid
   `X-Hub-Signature-256`, so this one is not optional.
4. `WHATSAPP_VERIFY_TOKEN`: any long random string you invent. It must not stay `etalase-dev-verify`.
5. **WhatsApp → Configuration → Webhook**: callback URL `https://<your service>/webhooks/whatsapp`, verify token = the string from
   step 4, then subscribe to the **messages** field. (After `make deploy` the URL is printed. Locally, use a tunnel such as
   `cloudflared tunnel --url http://localhost:8080` and set `PUBLIC_BASE_URL` to it.)
6. `make doctor` calls Meta with the token and prints the phone number it found.

Meta only lets a test number message the recipients you added, until the business is verified.

## 4. Xendit (QRIS)

1. <https://dashboard.xendit.co> → switch to **Test mode** → **Settings → API Keys** → generate a key with write access to
   *Money-in products* → `XENDIT_SECRET_KEY` (`xnd_development_...`).
2. **Settings → Webhooks**: copy the **Callback verification token** → `XENDIT_CALLBACK_TOKEN`. Register
   `https://<your service>/webhooks/payment` as the URL for QR code payments.
3. `make doctor` checks the key against the balance endpoint and warns if you pasted a live key while `ETALASE_ENV=dev`.

Use your Xendit account's current API reference to confirm QR Codes availability for your region; the adapter header
(`api-version`) and payload are in `prototype/etalase/adapters/payment.py`.

## 5. Biteship (courier rates)

1. <https://biteship.com> → create an account → **Integration / API keys** → create a **test** key
   (`biteship_test...`) → `BITESHIP_API_KEY`.
2. `make doctor` asks for a Jakarta to Bandung rate and prints the cheapest courier.

## 6. Instagram (optional)

Needed only to import a seller's real posts. The bundled sample posts already show the flow.

1. In your Meta app add **Instagram → API setup with Instagram login**.
2. Copy the **Instagram app ID** → `INSTAGRAM_APP_ID` and the **Instagram app secret** → `INSTAGRAM_APP_SECRET`.
3. Add `https://<your service>/connect/instagram/callback` as a valid OAuth redirect URI, and add your Instagram
   professional account as an Instagram tester. Public use needs Meta App Review; development mode works for testers.

## Everything else, `make help`

| Command | What it does |
|---|---|
| `make setup` | one-time local setup (safe to repeat) |
| `make doctor` / `make doctor-offline` | check every configured service; the offline variant makes no network call |
| `make eval` / `make eval-baseline` | catalog extraction accuracy (offline rules; plus Gemini when a key is set) |
| `make test` | the unit-test suite, no network, no keys |
| `make demo` | the whole flow in the terminal on mock services |
| `make e2e` | the same story over real HTTP: starts the app, then seller on WhatsApp, an agent over UCP and MCP, payment, shipping, payout |
| `make run` | API on `http://localhost:8080` (reads `.env`) |
| `make deploy` / `make deploy-dry` | Cloud Run deploy using `.env` / print the plan only |
| `make ucp-conformance` | Google's official UCP conformance suite against Etalase (needs `git`, `uv`, network) |
| `make ucp-reference` | the same suite against the official sample server (proves the suite itself runs) |
| `make ucp-server` | Etalase on `:8190` with fixed demo items, to try UCP requests by hand |
| `make academy` | build the documentation site |

## Before you submit

- The repository must be **public** (the rules require it). It is private now: flip it in GitHub → Settings → Danger zone.
- Put real numbers on the deck: the Gemini eval result, not the offline baseline.
- Fill the deck placeholders: team slide, screenshots, links.
- `make ucp-conformance` prints an honest scorecard; quote it as "runs the official suite", not as "certified".
