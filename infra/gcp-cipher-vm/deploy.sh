#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
VM_NAME="${VM_NAME:-cipher-main}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-2}"
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
NETWORK="${NETWORK:-cipher-vpc}"
SUBNET="${SUBNET:-cipher-subnet}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-cipher-vm-runtime}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="${BUCKET:-${PROJECT_ID}-cipher-runtime}"
STARTUP_SCRIPT="$ROOT/infra/gcp-cipher-vm/startup.sh"
ENV_FILE="$ROOT/cipher-system/app/.env"

if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
  echo "No active GCP project." >&2
  exit 1
fi
if [[ ! -f "$STARTUP_SCRIPT" ]]; then
  echo "Missing startup script: $STARTUP_SCRIPT" >&2
  exit 1
fi

echo "Project: $PROJECT_ID"
echo "VM:      $VM_NAME ($MACHINE_TYPE, ${DISK_SIZE_GB}GB)"
echo "Zone:    $ZONE"
echo "Bucket:  gs://$BUCKET"

gcloud services enable \
  compute.googleapis.com \
  iap.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com

if ! gcloud compute networks describe "$NETWORK" >/dev/null 2>&1; then
  gcloud compute networks create "$NETWORK" --subnet-mode=custom
fi
if ! gcloud compute networks subnets describe "$SUBNET" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute networks subnets create "$SUBNET" \
    --network "$NETWORK" \
    --region "$REGION" \
    --range 10.42.0.0/24
fi
if ! gcloud compute firewall-rules describe cipher-allow-iap-ssh >/dev/null 2>&1; then
  gcloud compute firewall-rules create cipher-allow-iap-ssh \
    --network "$NETWORK" \
    --direction INGRESS \
    --action ALLOW \
    --rules tcp:22 \
    --source-ranges 35.235.240.0/20 \
    --target-tags cipher-iap-ssh
fi

if ! gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --display-name "Cipher always-on VM runtime"
fi
for role in roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$RUNTIME_SA" \
    --role "$role" \
    --condition=None >/dev/null
 done

if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" \
    --location "$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNTIME_SA" \
  --role roles/storage.objectAdmin >/dev/null

read_env_value() {
  local name="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  python3 -c 'import pathlib,sys
path=pathlib.Path(sys.argv[1]); wanted=sys.argv[2]
for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line=raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key,value=line.split("=",1)
    if key.strip()==wanted:
        print(value.strip().strip("\"").strip("\x27"))
        break' "$ENV_FILE" "$name"
}

sync_secret() {
  local env_name="$1"
  local secret_name="$2"
  local value
  value="$(read_env_value "$env_name")"
  if [[ -z "$value" ]]; then
    echo "Skipping absent $env_name"
    return 0
  fi
  if gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$secret_name" --data-file=- >/dev/null
  else
    printf '%s' "$value" | gcloud secrets create "$secret_name" --replication-policy=automatic --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member "serviceAccount:$RUNTIME_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

sync_secret ALPACA_ALGO_KEY cipher-alpaca-algo-key
sync_secret ALPACA_ALGO_SECRET cipher-alpaca-algo-secret
sync_secret ALPACA_ALGO_PLUS_KEY cipher-alpaca-algo-plus-key
sync_secret ALPACA_ALGO_PLUS_SECRET cipher-alpaca-algo-plus-secret
sync_secret LSE_API_KEY cipher-lse-api-key
sync_secret CIPHER_APP_PASSWORD_HASH cipher-app-password-hash
sync_secret CLOUDFLARE_TUNNEL_TOKEN cipher-cloudflare-tunnel-token

if gcloud secrets describe tradier-access-token >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding tradier-access-token \
    --member "serviceAccount:$RUNTIME_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
fi

if ! gcloud compute instances describe "$VM_NAME" --zone "$ZONE" >/dev/null 2>&1; then
  gcloud compute instances create "$VM_NAME" \
    --zone "$ZONE" \
    --machine-type "$MACHINE_TYPE" \
    --image-family debian-12 \
    --image-project debian-cloud \
    --boot-disk-size "${DISK_SIZE_GB}GB" \
    --boot-disk-type pd-balanced \
    --network-interface "subnet=$SUBNET" \
    --service-account "$RUNTIME_SA" \
    --scopes cloud-platform \
    --tags cipher-iap-ssh \
    --metadata enable-oslogin=FALSE,block-project-ssh-keys=FALSE \
    --metadata-from-file startup-script="$STARTUP_SCRIPT" \
    --maintenance-policy MIGRATE \
    --restart-on-failure
else
  echo "VM $VM_NAME already exists; leaving machine configuration intact."
fi

echo "Waiting for bootstrap and IAP SSH..."
for attempt in $(seq 1 40); do
  if gcloud compute ssh "aarav@$VM_NAME" --zone "$ZONE" --tunnel-through-iap \
      --quiet --command 'test -f /var/lib/cipher/bootstrap-complete' >/dev/null 2>&1; then
    echo "VM bootstrap is complete."
    exit 0
  fi
  sleep 10
 done

echo "VM exists, but bootstrap did not complete within the polling window." >&2
echo "Inspect with: gcloud compute ssh aarav@$VM_NAME --zone $ZONE --tunnel-through-iap --command 'sudo tail -200 /var/log/cipher-bootstrap.log'" >&2
exit 2
