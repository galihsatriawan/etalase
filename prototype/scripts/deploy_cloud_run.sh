#!/usr/bin/env bash
# Deploy the prototype to Cloud Run (Jakarta region) with secrets from Secret Manager.
#
# One-time setup (as a project owner):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
#       secretmanager.googleapis.com aiplatform.googleapis.com firestore.googleapis.com
#   # only for STORE_BACKEND=firestore:
#   gcloud firestore databases create --location=asia-southeast2
#
# What this script does
#   1. Creates a dedicated service account (etalase-run) with only the roles it needs.
#   2. Creates SESSION_SECRET, DEV_TOKEN and WEBHOOK_TOKEN as random secrets (once; never printed).
#   3. Wires optional provider secrets if you created them (see OPTIONAL_SECRETS below).
#   4. Deploys from source with PUBLIC_BASE_URL already set to the service URL, so the startup
#      security checks (etalase/security.py) apply from the first revision.
#   Re-running is safe: it updates variables and secrets instead of replacing them.
#
# Optional live integrations: create a secret with the listed name, then re-run, e.g.
#   printf '%s' "$TOKEN" | gcloud secrets create etalase-whatsapp-token --data-file=-
#   and pass non-secret ids as env vars: WHATSAPP_PHONE_NUMBER_ID=... INSTAGRAM_APP_ID=... ./scripts/deploy_cloud_run.sh
#   Note: the app refuses to start if WHATSAPP_TOKEN is set without WHATSAPP_APP_SECRET and a
#   non-default WHATSAPP_VERIFY_TOKEN, or XENDIT_SECRET_KEY without XENDIT_CALLBACK_TOKEN.
#
# Usage:  PROJECT_ID=my-project ./scripts/deploy_cloud_run.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-southeast2}"
SERVICE="${SERVICE:-etalase}"
ENV_MODE="${ETALASE_ENV:-dev}"   # "dev" for the hackathon demo (mock payment/shipping + /dev endpoints behind DEV_TOKEN)
SA_NAME="etalase-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------- service account (least privilege)
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" --display-name "Etalase Cloud Run"
fi
for role in roles/aiplatform.user roles/datastore.user roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role "$role" \
    --condition=None --quiet >/dev/null
done

# ---------------------------------------------------------------- secrets
ensure_secret() {  # create a random secret once; never print it
  if ! gcloud secrets describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" |
      gcloud secrets create "$1" --project "$PROJECT_ID" --replication-policy=automatic --data-file=-
  fi
}
ensure_secret etalase-session-secret
ensure_secret etalase-dev-token
ensure_secret etalase-webhook-token
SECRETS="SESSION_SECRET=etalase-session-secret:latest,DEV_TOKEN=etalase-dev-token:latest,WEBHOOK_TOKEN=etalase-webhook-token:latest"

# ENV_VAR=secret-name pairs, wired only if the secret exists
OPTIONAL_SECRETS=(
  "WHATSAPP_TOKEN=etalase-whatsapp-token"
  "WHATSAPP_APP_SECRET=etalase-whatsapp-app-secret"
  "WHATSAPP_VERIFY_TOKEN=etalase-whatsapp-verify-token"
  "XENDIT_SECRET_KEY=etalase-xendit-key"
  "XENDIT_CALLBACK_TOKEN=etalase-xendit-callback"
  "BITESHIP_API_KEY=etalase-biteship-key"
  "INSTAGRAM_APP_SECRET=etalase-instagram-app-secret"
)
for pair in "${OPTIONAL_SECRETS[@]}"; do
  if gcloud secrets describe "${pair#*=}" --project "$PROJECT_ID" >/dev/null 2>&1; then
    SECRETS="${SECRETS},${pair}:latest"
    echo "wiring optional secret ${pair#*=}"
  fi
done

# ---------------------------------------------------------------- environment
# Cloud Run URLs are deterministic: https://SERVICE-PROJECT_NUMBER.REGION.run.app
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

ENV_VARS="ETALASE_ENV=${ENV_MODE},PUBLIC_BASE_URL=${URL},STORE_BACKEND=${STORE_BACKEND:-memory}"
ENV_VARS="${ENV_VARS},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
ENV_VARS="${ENV_VARS},GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash},SELLER_LOCALE=${SELLER_LOCALE:-en}"
[[ -n "${WHATSAPP_PHONE_NUMBER_ID:-}" ]] && ENV_VARS="${ENV_VARS},WHATSAPP_PHONE_NUMBER_ID=${WHATSAPP_PHONE_NUMBER_ID}"
[[ -n "${INSTAGRAM_APP_ID:-}" ]] && ENV_VARS="${ENV_VARS},INSTAGRAM_APP_ID=${INSTAGRAM_APP_ID}"

# ---------------------------------------------------------------- deploy
# --no-cpu-throttling keeps CPU for the in-process reservation-expiry loop between requests.
# --max-instances 1 because the demo uses the in-memory store (state lives in one instance).
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --no-cpu-throttling \
  --update-env-vars "$ENV_VARS" \
  --update-secrets "$SECRETS"

ACTUAL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
if [[ "$ACTUAL" != "$URL" ]]; then  # older projects may get the legacy URL format
  gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
    --update-env-vars "PUBLIC_BASE_URL=${ACTUAL}"
  URL="$ACTUAL"
fi

cat <<EOF
Deployed: ${URL}
  Health:               curl ${URL}/healthz
  MCP for agents:       ${URL}/mcp/
  WhatsApp webhook:     ${URL}/webhooks/whatsapp   (Meta verify token = WHATSAPP_VERIFY_TOKEN)
  Payment webhook:      ${URL}/webhooks/payment    (Xendit sends its callback token; the mock needs ?token=)
  Shipping webhook:     ${URL}/webhooks/shipping?token=<WEBHOOK_TOKEN>
  Expiry (optional Cloud Scheduler, every minute):  POST ${URL}/tasks/expire?token=<WEBHOOK_TOKEN>
  Dev endpoints header: X-Dev-Token: \$(gcloud secrets versions access latest --secret etalase-dev-token --project ${PROJECT_ID})
  Webhook token:        gcloud secrets versions access latest --secret etalase-webhook-token --project ${PROJECT_ID}
For the pilot: STORE_BACKEND=firestore, lift --max-instances, and move expiry to Cloud Tasks.
EOF
