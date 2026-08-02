#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-$(gcloud config get-value run/region 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-cipher-tradier-stream}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-cipher-tradier-stream}"
SECRET_NAME="${SECRET_NAME:-tradier-access-token}"
SCHEDULER_JOB="${SCHEDULER_JOB:-cipher-tradier-stream-hourly}"
SCHEDULER_SA="${SCHEDULER_SA:-cipher-scheduler-invoker}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "PROJECT_ID is not set. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Service: ${SERVICE_NAME}"
echo "Bucket: gs://${BUCKET_NAME}"
echo

gcloud config set project "${PROJECT_ID}" >/dev/null
gcloud config set run/region "${REGION}" >/dev/null

echo "Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com

echo "Creating GCS bucket if missing..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" \
    --uniform-bucket-level-access
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
SOURCE_BUCKET="run-sources-${PROJECT_ID}-${REGION}"

echo "Granting build/runtime service-account permissions needed by Cloud Run source deploy..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/artifactregistry.writer \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/logging.logWriter \
  --condition=None >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/storage.objectCreator >/dev/null

echo "Creating/updating Tradier token secret."
echo "Paste the Tradier production token when prompted. Input will not be echoed by the shell."
if gcloud secrets describe "${SECRET_NAME}" >/dev/null 2>&1; then
  read -r -s -p "Tradier production token: " TRADIER_TOKEN
  echo
  printf "%s" "${TRADIER_TOKEN}" | gcloud secrets versions add "${SECRET_NAME}" --data-file=-
else
  read -r -s -p "Tradier production token: " TRADIER_TOKEN
  echo
  printf "%s" "${TRADIER_TOKEN}" | gcloud secrets create "${SECRET_NAME}" --data-file=-
fi
unset TRADIER_TOKEN

echo "Granting Cloud Run runtime access to the Tradier token secret..."
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/secretmanager.secretAccessor >/dev/null

echo "Deploying Cloud Run service..."
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --timeout 3600 \
  --max-instances 1 \
  --min-instances 0 \
  --no-allow-unauthenticated \
  --set-secrets "TRADIER_ACCESS_TOKEN=${SECRET_NAME}:latest" \
  --set-env-vars "GCS_BUCKET=${BUCKET_NAME},MAX_CAPTURE_SECONDS=3300"

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format='value(status.url)')"

if gcloud storage buckets describe "gs://${SOURCE_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets add-iam-policy-binding "gs://${SOURCE_BUCKET}" \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/storage.objectViewer >/dev/null
fi

echo "Creating Scheduler invoker service account if missing..."
if ! gcloud iam service-accounts describe "${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SCHEDULER_SA}" \
    --display-name "Cipher Scheduler Cloud Run Invoker"
fi

echo "Granting Cloud Run invoker role..."
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --region "${REGION}" \
  --member "serviceAccount:${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/run.invoker >/dev/null

echo "Creating/updating hourly market-hours Scheduler job..."
SCHEDULER_CAPTURE_SECONDS="${SCHEDULER_CAPTURE_SECONDS:-120}"
SCHEDULER_URI="${SERVICE_URL}/capture?symbols=SPY,QQQ,IWM,NVDA,MSFT,AAPL,AVGO,AMZN,IBIT,GOOGL,TSLA,META,MU,AMD&duration=${SCHEDULER_CAPTURE_SECONDS}"
if gcloud scheduler jobs describe "${SCHEDULER_JOB}" --location "${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
    --location "${REGION}" \
    --schedule "0 8-16 * * 1-5" \
    --time-zone "America/New_York" \
    --uri "${SCHEDULER_URI}" \
    --http-method GET \
    --oidc-service-account-email "${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --oidc-token-audience "${SERVICE_URL}" \
    --attempt-deadline 180s
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
    --location "${REGION}" \
    --schedule "0 8-16 * * 1-5" \
    --time-zone "America/New_York" \
    --uri "${SCHEDULER_URI}" \
    --http-method GET \
    --oidc-service-account-email "${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --oidc-token-audience "${SERVICE_URL}" \
    --attempt-deadline 180s
fi

echo
echo "Done."
echo "Service URL: ${SERVICE_URL}"
echo "Health check after auth/deploy:"
echo "  gcloud run services describe ${SERVICE_NAME} --region ${REGION}"
echo "Manual capture test:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \"${SERVICE_URL}/capture?symbols=SPY,QQQ,IWM&duration=30\""
