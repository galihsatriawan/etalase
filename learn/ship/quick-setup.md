# Quick setup: only the keys are left

One command prepares the project, one command tests your keys, one command deploys. This page is the short version of
[`SETUP.md`](https://github.com/galihsatriawan/etalase/blob/main/SETUP.md) in the repository, which lists where each value comes from.

::: tip What you will learn
- The four commands that cover setup, key checking, accuracy measurement and deployment
- Which keys are mandatory (almost none) and what each optional key buys
- How the deploy copies your `.env` into Secret Manager without ever printing a value
:::

## The four commands

```bash
cd prototype
make setup     # venv, dependencies, .env with a random SESSION_SECRET, offline checks, tests
make doctor    # tests every key you filled in against the real service
make eval      # extraction accuracy: offline rules, plus Gemini when a key is set
PROJECT_ID=my-project make deploy   # Cloud Run from your .env; `make deploy-dry` prints the plan only
```

`make e2e` plays the whole story over real HTTP against a server it starts itself, so you can see it work before any key is filled in. `make help` lists the rest (demo, run, UCP conformance, building this site).

## What to fill in

Every key is optional. A missing one means the app uses a mock for that part, so the demo always runs.

| Fill in | Without it | What it unlocks |
|---|---|---|
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) or a Google Cloud project (Vertex AI) | Rule-based extraction, 49% of the 43 labelled messages fully correct | Gemini extraction and the ADK agents: the Gen AI score |
| A Google Cloud project with billing | Runs on your laptop only | The public prototype link the submission asks for |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, plus four message templates created in WhatsApp Manager ([guide](/ship/whatsapp-templates)) | Orders print in the terminal | The order landing in the seller's WhatsApp, also outside the 24-hour window |
| `XENDIT_SECRET_KEY`, `XENDIT_CALLBACK_TOKEN` | Mock QRIS | Real QR codes (test mode is enough) |
| `BITESHIP_API_KEY` (test key, small balance: test mode is billed) | Mock courier rates | Real rates, booking and tracking |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | Sample posts | Importing a seller's real posts |

The minimum for a convincing submission is Gemini plus a deploy. The rest makes the demo more real.

## What the doctor does

`make doctor` is the reason you never find out in front of a judge that a key was wrong. For every service that has
credentials it makes one real, harmless call: a Gemini extraction of a sample message, a WhatsApp phone-number lookup, the Xendit
balance, a Biteship rate for Jakarta to Bandung. For every service without credentials it says "skip" and what to set.
It also builds the app exactly as the server does, so the fail-closed startup security checks run, and prints the webhook URLs
to register in each dashboard. It never prints a key value. `make doctor-offline` makes no network call at all.

The checks live in `prototype/etalase/doctor.py` and are tested with the network mocked in `tests/test_doctor.py`.

## What the deploy does with your `.env`

`scripts/deploy_cloud_run.sh` calls `scripts/env_to_cloud.py`, which splits your `.env` in two:

- **Secrets** (API keys, tokens) go to Secret Manager. The value reaches `gcloud` on **stdin**, never on a command line and never in the
  output. A secret whose value did not change gets no new version, so re-running the deploy is cheap.
- **Everything else** (phone number id, model name, limits) becomes a plain environment variable, joined with gcloud's `^@^` delimiter so
  values may contain commas.

Settings the deploy decides itself are never copied from your laptop: `ETALASE_ENV`, `PUBLIC_BASE_URL`, `STORE_BACKEND`, and the random `SESSION_SECRET`,
`DEV_TOKEN` and `WEBHOOK_TOKEN`. With `GOOGLE_API_KEY` in `.env` the service uses that key; without it, Vertex AI through the service
account. `tests/test_scripts.py` checks all of this with a fake `gcloud` on the PATH, including that no secret appears in the output.

::: warning What this sandbox could not test
The doctor and the deploy talk to Google, Meta, Xendit and Biteship, which the build sandbox cannot reach. The request and response shapes were
compared with each provider's public documentation and are pinned by contract tests with a fake HTTP layer (`tests/test_gemini.py`,
`test_whatsapp.py`, `test_xendit.py`, `test_biteship.py`); the deploy is tested against mocks and `DRY_RUN`. That proves what Etalase sends and reads,
not that a provider accepts it. The first real run is yours; if a provider changed an endpoint, `make doctor` is where it will show.
Xendit's `/qr_codes` endpoint is marked legacy by Xendit and its current reference could not be opened, so treat that payload as the most likely to need a tweak.
:::

## Check yourself

1. Which keys are mandatory to run the demo? *None.* Which one matters most for the score? *Gemini.*
2. Why does the deploy pass secrets on stdin? *A command line is visible in process lists and shell history.*
3. What does `make doctor-offline` still check? *The configuration and the startup security checks, without any network call.*

## Next

[Deploy to Cloud Run](/ship/deploy) explains the service in detail, and [Turn on real services](/ship/real-services) covers the provider side.
