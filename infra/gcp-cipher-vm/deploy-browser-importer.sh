#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/home/aarav/Aarav/cipher"
SYSTEMD_DIR="$ROOT/infra/gcp-cipher-vm/systemd"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
IMPORTER="$ROOT/cipher-system/scripts/import_browser_gcs_payloads.py"

if [[ ! -x "$PYTHON" ]]; then
  echo "Cipher virtualenv Python is missing: $PYTHON" >&2
  exit 1
fi
if [[ ! -f "$IMPORTER" ]]; then
  echo "Browser importer is missing: $IMPORTER" >&2
  exit 1
fi

"$PYTHON" -m compileall -q "$IMPORTER"
"$PYTHON" "$IMPORTER" --dry-run >/tmp/cipher-browser-import-dry-run.json

install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-import.service" \
  /etc/systemd/system/cipher-browser-import.service
install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-import.timer" \
  /etc/systemd/system/cipher-browser-import.timer

systemctl daemon-reload
systemctl enable --now cipher-browser-import.timer
systemctl start cipher-browser-import.service

systemctl is-enabled cipher-browser-import.timer
systemctl is-active cipher-browser-import.timer
systemctl status cipher-browser-import.service --no-pager --lines=20 || true

sqlite3 \
  "$ROOT/cipher-system/data/browser_ingest/gcs_import_ledger.sqlite" \
  "select scan_type, status, count(*) from imported_batches group by scan_type, status order by scan_type, status;"
