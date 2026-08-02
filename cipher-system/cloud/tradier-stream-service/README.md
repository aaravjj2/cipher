# Tradier Stream Cloud Service

Read-only Cloud Run collector for Tradier market-data streaming. It captures
short streaming windows, writes raw JSONL locally in the container, and uploads
to GCS when `GCS_BUCKET` is set.

It does not call account, preview, order, or trading endpoints.

## Recommended Cloud

Use Google Cloud Run plus Cloud Scheduler:

- Cloud Run handles the HTTP-triggered capture worker.
- Cloud Scheduler calls `/capture` each market hour.
- Secret Manager stores `TRADIER_ACCESS_TOKEN`.
- GCS stores raw JSONL event files.
- BigQuery can be added later over the GCS event lake for research queries.

Cloud Run is a good fit because Tradier only allows one market stream session at
a time. Deploy with `--max-instances=1` so two collectors do not compete.

## Deploy

Run from this directory:

```bash
gcloud secrets create tradier-access-token --data-file=-
gcloud storage buckets create gs://YOUR_CIPHER_TRADIER_BUCKET --location=us-central1
gcloud run deploy cipher-tradier-stream \
  --source . \
  --region us-central1 \
  --timeout 3600 \
  --max-instances 1 \
  --min-instances 0 \
  --no-allow-unauthenticated \
  --set-secrets TRADIER_ACCESS_TOKEN=tradier-access-token:latest \
  --set-env-vars GCS_BUCKET=YOUR_CIPHER_TRADIER_BUCKET,MAX_CAPTURE_SECONDS=3300
```

Then create a scheduler job with an OIDC service account allowed to invoke the
Cloud Run service:

```bash
SERVICE_URL="$(gcloud run services describe cipher-tradier-stream --region us-central1 --format='value(status.url)')"

gcloud scheduler jobs create http cipher-tradier-stream-hourly \
  --location us-central1 \
  --schedule "0 8-16 * * 1-5" \
  --time-zone "America/New_York" \
  --uri "${SERVICE_URL}/capture?symbols=SPY,QQQ,IWM&duration=120" \
  --http-method GET \
  --oidc-service-account-email YOUR_SCHEDULER_INVOKER@YOUR_PROJECT.iam.gserviceaccount.com \
  --oidc-token-audience "${SERVICE_URL}" \
  --attempt-deadline 180s
```

For a quick smoke check:

```bash
curl "${SERVICE_URL}/health"
curl "${SERVICE_URL}/capture?symbols=SPY,QQQ,IWM&duration=30"
```

## Limits

Cloud Run HTTP services default to a 300 second timeout and can be configured up
to 3600 seconds. Cloud Scheduler HTTP jobs have their own shorter attempt
deadline, so the hourly job uses a reliable 120 second stream window. Longer
captures can still be run manually against Cloud Run.

Tradier streaming sessions must be created before connecting, and the session id
has to be used quickly. Tradier also says one market stream session at a time is
allowed, so do not deploy multiple instances of this service.
