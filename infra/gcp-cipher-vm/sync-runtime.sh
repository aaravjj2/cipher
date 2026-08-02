#!/usr/bin/env bash
# shellcheck disable=SC2086
set -euo pipefail

# Ensure zstd is in PATH (conda, /usr/local, /usr/bin)
export PATH="/home/aarav/miniconda3/bin:/usr/local/bin:/usr/bin:$PATH"

# ── Cipher runtime archive builder ──────────────────────────────────────
# Creates a deterministic operational snapshot, uploads to GCS for
# resumable download by the VM.  Supports re-runs.
#
# Usage:  ./sync-runtime.sh [--skip-upload]
# ─────────────────────────────────────────────────────────────────────────

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
VM_NAME="${VM_NAME:-cipher-main}"
BUCKET="${BUCKET:-${PROJECT_ID}-cipher-runtime}"
STAGING_KEY="staging/cipher-runtime.tar.zst"
SKIP_UPLOAD=0
[[ "${1:-}" == "--skip-upload" ]] && SKIP_UPLOAD=1

if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
  echo "ERROR: No active GCP project." >&2; exit 1
fi

WORK="$(mktemp -d -t cipher-migration-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

RUNTIME_ARCHIVE="$WORK/cipher-runtime.tar.zst"
DEVSPACE_ARCHIVE="$WORK/devspace-state.tar.zst"
HERMES_BACKUP="$WORK/hermes-quick.zip"
MANIFEST="$WORK/migration-manifest.txt"

# ── 1. Build operational archive ────────────────────────────────────────
echo "▶ Building operational Cipher snapshot..."
tar --zstd -cf "$RUNTIME_ARCHIVE" \
  --exclude='./.git' \
  --exclude='./Stock data' \
  --exclude='./.venv' \
  --exclude='./node_modules' \
  --exclude='./.codex-tmp' \
  --exclude='./.pytest_cache' \
  --exclude='./.agents' \
  --exclude='./.devspace' \
  --exclude='./.playwright-mcp' \
  --exclude='./.qoder' \
  --exclude='./cipher-system.zip' \
  --exclude='./cipher-system/previous-work' \
  --exclude='./cipher-system/obsidian-commercial-csv-weight-dataset*.zip' \
  --exclude='./cipher-system/data/historical_options' \
  --exclude='./cipher-system/data/historical_equities' \
  --exclude='./cipher-system/data/timesfm_model' \
  --exclude='./cipher-system/data/timesfm_training' \
  --exclude='./cipher-system/data/governance' \
  --exclude='./cipher-system/data/raw_lake' \
  --exclude='./cipher-system/data/research_snapshots' \
  --exclude='./cipher-system/data/warehouse_exports' \
  --exclude='./cipher-system/data/flow_cluster_history.sqlite' \
  --exclude='./cipher-system/data/flow_forward_test.sqlite' \
  --exclude='./cipher-system/data/flow_clusters' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='./cipher.egg-info' \
  --exclude='./parity-snapshots' \
  --exclude='./strike matrix csvs' \
  --exclude='./night vision charts' \
  --exclude='./data' \
  --exclude='./reports' \
  --exclude='./scripts' \
  --exclude='./tests' \
  --exclude='./ao_automation' \
  --exclude='./flash_automation' \
  --exclude='./quantconnect' \
  --exclude='./config' \
  --exclude='./docs' \
  --exclude='./vendor' \
  --exclude='*.log' \
  --exclude='*.err.log' \
  --exclude='*.out.log' \
  --exclude='./chatgpt_*' \
  --exclude='./.nvmrc' \
  --exclude='./lean.json' \
  --exclude='./pyproject.toml' \
  --exclude='./requirements-dev.txt' \
  --exclude='./server.py' \
  --exclude='./bulk_*.py' \
  --exclude='./.zipignore' \
  -C "$ROOT" .

RUNTIME_HASH="$(sha256sum "$RUNTIME_ARCHIVE" | awk '{print $1}')"
RUNTIME_SIZE="$(stat -c%s "$RUNTIME_ARCHIVE" 2>/dev/null || stat -f%z "$RUNTIME_ARCHIVE")"
echo "  archive: $(basename "$RUNTIME_ARCHIVE")  size=${RUNTIME_SIZE}  sha256=${RUNTIME_HASH}"

# ── 2. DevSpace state (if present) ─────────────────────────────────────
if [[ -d /home/aarav/.devspace ]]; then
  echo "▶ Archiving DevSpace state..."
  tar --zstd -cf "$DEVSPACE_ARCHIVE" \
    -C /home/aarav .devspace .local/share/devspace 2>/dev/null || true
fi

# ── 3. Hermes quick backup (if available) ──────────────────────────────
if command -v hermes >/dev/null 2>&1; then
  echo "▶ Creating Hermes quick backup..."
  hermes backup --quick --output "$HERMES_BACKUP" || true
fi

# ── 4. Manifest ─────────────────────────────────────────────────────────
{
  printf 'source_host=%s\n' "$(hostname)"
  printf 'source_head=%s\n' "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  printf 'created_at=%s\n' "$(date -u +%FT%TZ)"
  printf 'runtime_sha256=%s\n' "$RUNTIME_HASH"
  printf 'runtime_size=%s\n' "$RUNTIME_SIZE"
} > "$MANIFEST"

# ── 5. Upload to GCS staging (resumable) ───────────────────────────────
if [[ "$SKIP_UPLOAD" -eq 0 ]]; then
  echo "▶ Uploading runtime archive to GCS (resumable)..."
  gsutil -o GSUtil:resumable_upload_threshold=0 \
    cp "$RUNTIME_ARCHIVE" "gs://${BUCKET}/${STAGING_KEY}"
  echo "  gs://${BUCKET}/${STAGING_KEY}"

  if [[ -f "$DEVSPACE_ARCHIVE" ]]; then
    echo "▶ Uploading DevSpace state..."
    gsutil cp "$DEVSPACE_ARCHIVE" "gs://${BUCKET}/staging/devspace-state.tar.zst"
  fi
  if [[ -f "$HERMES_BACKUP" ]]; then
    echo "▶ Uploading Hermes backup..."
    gsutil cp "$HERMES_BACKUP" "gs://${BUCKET}/staging/hermes-quick.zip"
  fi

  # Upload manifest
  gsutil cp "$MANIFEST" "gs://${BUCKET}/staging/migration-manifest.txt"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo " Migration archive ready"
echo " SHA-256: ${RUNTIME_HASH}"
echo " Size:    ${RUNTIME_SIZE} bytes"
echo " GCS:     gs://${BUCKET}/${STAGING_KEY}"
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "Next: run configure-vm.sh on the VM to extract and install."
