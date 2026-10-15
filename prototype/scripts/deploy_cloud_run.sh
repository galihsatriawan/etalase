#!/usr/bin/env bash
# Deploy the prototype to Cloud Run (Jakarta region), taking your API keys from the local .env file.
#
#   PROJECT_ID=my-project ./scripts/deploy_cloud_run.sh            # deploy
#   PROJECT_ID=my-project DRY_RUN=1 ./scripts/deploy_cloud_run.sh  # print every step, change nothing
#
# Run it from Cloud Shell (gcloud is already signed in) or after `gcloud auth login`.
#
# What it does
#   1. Enables the Google Cloud APIs it needs.
#   2. Creates a service account (etalase-run) with only the roles it needs.
#   3. Creates SESSION_SECRET, DEV_TOKEN and WEBHOOK_TOKEN as random secrets (once; never printed).
#   4. Copies the keys you filled in .env into Secret Manager and the other settings into environment
#      variables (scripts/env_to_cloud.py; values are never printed).
#   5. Deploys from source with PUBLIC_BASE_URL already set to the service URL, so the startup security
#      checks (etalase/security.py) apply from the first revision, then checks /healthz and /.well-known/ucp.
#   Re-running is safe: it updates variables and secrets instead of replacing them.
#
# Gemini: with GOOGLE_API_KEY in .env the service uses that key; otherwise Vertex AI through the service account.
# Options (environment variables): REGION, SERVICE, ETALASE_ENV (default dev), STORE_BACKEND (default memory),
#   ENV_FILE (default .env), GEMINI_AUTH (auto|vertex|apikey), SKIP_API_ENABLE=1, SKIP_SMOKE=1.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID, for example: PROJECT_ID=my-project ./scripts/deploy_cloud_run.sh}"
REGION="${REGION:-asia-southeast2}"
SERVICE="${SERVICE:-etalase}"
ENV_MODE="${ETALASE_ENV:-dev}"       # "dev" for the demo: mock payment/shipping and /dev endpoints behind DEV_TOKEN
ENV_FILE="${ENV_FILE:-.env}"
SA_NAME="etalase-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-}"

cd "$(dirname "$0")/.."

run() {  # print instead of run when DRY_RUN is set
  if [[ -n "$DRY_RUN" ]]; then echo "+ $*"; else "$@"; fi
}
say() { printf '\n== %s\n' "$*"; }

if [[ -z "$DRY_RUN" ]]; then
  command -v gcloud >/dev/null || { echo "gcloud is not installed. Use Cloud Shell, or install the Google Cloud CLI."; exit 1; }
fi
[[ -f "$ENV_FILE" ]] || echo "Note: no $ENV_FILE found. Deploying with mock services only (run 'make setup' and fill it in to change that)."

# ---------------------------------------------------------------- APIs
if [[ -z "${SKIP_API_ENABLE:-}" ]]; then
  say "Enabling APIs"
  run gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
      secretmanager.googleapis.com aiplatform.googleapis.com firestore.googleapis.com --project "$PROJECT_ID" ||
    echo "Could not enable the APIs (missing permission?). Continuing; the deploy fails below if one is really off."
fi

# ---------------------------------------------------------------- service account (least privilege)
say "Service account $SA"
if [[ -n "$DRY_RUN" ]] || ! gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  run gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" --display-name "Etalase Cloud Run"
fi
for role in roles/aiplatform.user roles/datastore.user roles/secretmanager.secretAccessor roles/logging.logWriter; do
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role "$role" \
      --condition=None --quiet >/dev/null
done

# ---------------------------------------------------------------- generated secrets
say "Generated secrets"
ensure_secret() {  # create a random secret once; never print it
  if [[ -n "$DRY_RUN" ]]; then echo "+ create secret $1 if missing (random value)"; return; fi
  if ! gcloud secrets describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" |
      gcloud secrets create "$1" --project "$PROJECT_ID" --replication-policy=automatic --data-file=-
  fi
}
ensure_secret etalase-session-secret
ensure_secret etalase-dev-token
ensure_secret etalase-webhook-token

# ---------------------------------------------------------------- URL (deterministic on Cloud Run)
if [[ -n "$DRY_RUN" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)' 2>/dev/null || echo 000000000000)"
else
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
fi
URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

# ---------------------------------------------------------------- your keys and settings from .env
say "Keys and settings from $ENV_FILE (names only; values are never printed)"
SYNC="$(python3 scripts/env_to_cloud.py --project "$PROJECT_ID" --env-file "$ENV_FILE" --gemini-auth "${GEMINI_AUTH:-auto}" \
  ${DRY_RUN:+--dry-run} \
  --default SELLER_LOCALE=en --default GEMINI_MODEL=gemini-2.5-flash --default GOOGLE_CLOUD_LOCATION=asia-southeast1 \
  --fixed "ETALASE_ENV=${ENV_MODE}" --fixed "PUBLIC_BASE_URL=${URL}" --fixed "STORE_BACKEND=${STORE_BACKEND:-memory}" \
  --fixed "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}")"
ENV_VARS="$(printf '%s\n' "$SYNC" | sed -n 's/^ENV_VARS=//p')"
SECRETS="SESSION_SECRET=etalase-session-secret:latest,DEV_TOKEN=etalase-dev-token:latest,WEBHOOK_TOKEN=etalase-webhook-token:latest"
EXTRA_SECRETS="$(printf '%s\n' "$SYNC" | sed -n 's/^SECRETS=//p')"
[[ -n "$EXTRA_SECRETS" ]] && SECRETS="${SECRETS},${EXTRA_SECRETS}"

# ---------------------------------------------------------------- deploy
# --no-cpu-throttling keeps CPU for the in-process reservation-expiry loop between requests.
# --max-instances 1 because the demo uses the in-memory store (state lives in one instance).
say "Deploying $SERVICE to Cloud Run ($REGION)"
run gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --no-cpu-throttling \
  --update-env-vars "^@^${ENV_VARS}" \
  --update-secrets "$SECRETS"

if [[ -z "$DRY_RUN" ]]; then
  ACTUAL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
  if [[ "$ACTUAL" != "$URL" ]]; then  # older projects may get the legacy URL format
    gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
      --update-env-vars "PUBLIC_BASE_URL=${ACTUAL}"
    URL="$ACTUAL"
  fi
  if [[ -z "${SKIP_SMOKE:-}" ]]; then
    say "Smoke test"
    curl -fsS "${URL}/healthz" && echo
    curl -fsS "${URL}/.well-known/ucp" | python3 -c "import json,sys; d=json.load(sys.stdin)['ucp']; print('UCP profile OK, version', d['version'], '-', ', '.join(d['capabilities']))"
  fi
fi

[[ -n "$DRY_RUN" ]] && echo && echo "DRY RUN: nothing above was executed. The service would be at:"
cat <<EOF

${DRY_RUN:+(dry run) }Deployed: ${URL}
  Health:               curl ${URL}/healthz
  MCP for agents:       ${URL}/mcp/
  UCP profile:          ${URL}/.well-known/ucp   (checkout API: ${URL}/ucp/v1)
  WhatsApp webhook:     ${URL}/webhooks/whatsapp   (Meta verify token = WHATSAPP_VERIFY_TOKEN)
  Payment webhook:      ${URL}/webhooks/payment    (Xendit sends its callback token; the mock needs ?token=)
  Shipping webhook:     ${URL}/webhooks/shipping?token=<WEBHOOK_TOKEN>
  Expiry (optional Cloud Scheduler, every minute):  POST ${URL}/tasks/expire?token=<WEBHOOK_TOKEN>
  Dev endpoints header: X-Dev-Token: \$(gcloud secrets versions access latest --secret etalase-dev-token --project ${PROJECT_ID})
  Webhook token:        gcloud secrets versions access latest --secret etalase-webhook-token --project ${PROJECT_ID}
For the pilot: STORE_BACKEND=firestore, lift --max-instances, and move expiry to Cloud Tasks.
EOF
