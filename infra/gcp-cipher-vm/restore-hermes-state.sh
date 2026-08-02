#!/usr/bin/env bash
# Restore the approved Hermes state snapshot onto cipher-main without
# overwriting the pinned Hermes code checkout or virtual environment.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-eec91607-77a2-4be6-837}"
BUCKET="${BUCKET:-${PROJECT_ID}-cipher-runtime}"
OBJECT="${HERMES_STATE_OBJECT:-staging/hermes-state.tar.zst}"
HERMES_HOME="/home/aarav/.hermes"
HERMES_REAL="$HERMES_HOME/hermes-agent/venv/bin/hermes"
HERMES_LINK="/home/aarav/.local/bin/hermes"

if [[ ! -x "$HERMES_REAL" ]]; then
  echo "ERROR: real Hermes CLI is missing: $HERMES_REAL" >&2
  exit 1
fi

work="$(mktemp -d -t cipher-hermes-restore-XXXXXX)"
trap 'rm -rf "$work"' EXIT
archive="$work/hermes-state.tar.zst"
extract="$work/extract"
mkdir -p "$extract"

echo "Downloading Hermes state snapshot..."
gsutil -q cp "gs://${BUCKET}/${OBJECT}" "$archive"

# Reject absolute paths, parent traversal, and archives with multiple roots.
mapfile -t entries < <(tar --zstd -tf "$archive")
if [[ "${#entries[@]}" -eq 0 ]]; then
  echo "ERROR: Hermes state archive is empty." >&2
  exit 1
fi
for entry in "${entries[@]}"; do
  if [[ "$entry" == /* || "$entry" == *"../"* || "$entry" == ".." ]]; then
    echo "ERROR: unsafe archive path detected." >&2
    exit 1
  fi
done
root="${entries[0]%%/*}"
if [[ -z "$root" ]]; then
  echo "ERROR: snapshot has no top-level directory." >&2
  exit 1
fi
for entry in "${entries[@]}"; do
  [[ "$entry" == "$root" || "$entry" == "$root/"* ]] || {
    echo "ERROR: snapshot contains multiple top-level roots." >&2
    exit 1
  }
done

tar --zstd -xf "$archive" -C "$extract"
snapshot="$extract/$root"
[[ -d "$snapshot" ]] || { echo "ERROR: extracted snapshot root missing." >&2; exit 1; }

# Preserve the VM's current state before replacement.  The code checkout and
# venv are intentionally excluded.
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$HERMES_HOME/cipher-pre-restore-${stamp}.tar.zst"
mkdir -p "$HERMES_HOME"
tar --zstd -cf "$backup" -C "$HERMES_HOME" \
  --exclude=hermes-agent \
  --exclude='cipher-pre-restore-*.tar.zst' \
  . 2>/dev/null || true

approved=(
  .env
  auth.json
  config.yaml
  state.db
  response_store.db
  gateway_state.json
  projects.db
  verification_evidence.db
  processes.json
  channel_directory.json
  kanban.db
  cron
)

for item in "${approved[@]}"; do
  if [[ -e "$snapshot/$item" ]]; then
    rm -rf "$HERMES_HOME/$item"
    cp -a "$snapshot/$item" "$HERMES_HOME/$item"
  fi
done

install -d -m 0755 -o aarav -g aarav /home/aarav/.local/bin
ln -sfn "$HERMES_REAL" "$HERMES_LINK"
chown -h aarav:aarav "$HERMES_LINK"
chown -R aarav:aarav "$HERMES_HOME"
chmod 700 "$HERMES_HOME"
chmod 600 "$HERMES_HOME/.env" "$HERMES_HOME/auth.json" "$HERMES_HOME/config.yaml" 2>/dev/null || true
chmod 600 "$HERMES_HOME"/*.db 2>/dev/null || true
chmod 700 "$HERMES_HOME/cron" 2>/dev/null || true
chmod 600 "$HERMES_HOME/cron"/* 2>/dev/null || true

touch "$HERMES_HOME/.cipher-state-restored"
chown aarav:aarav "$HERMES_HOME/.cipher-state-restored"
chmod 600 "$HERMES_HOME/.cipher-state-restored"

echo "Hermes state restored."
"$HERMES_REAL" --version
"$HERMES_REAL" status >/dev/null
