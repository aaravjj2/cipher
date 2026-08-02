#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/home/aarav/Aarav/cipher"
SYSTEMD_DIR="$ROOT/infra/gcp-cipher-vm/systemd"
BIN_DIR="$ROOT/infra/gcp-cipher-vm/bin"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
IMPORTER="$ROOT/cipher-system/scripts/import_browser_gcs_payloads.py"

install -m 0755 \
  "$BIN_DIR/sync-browser-folders-from-gcs.sh" \
  /usr/local/lib/cipher/sync-browser-folders-from-gcs.sh

install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-folder-sync.service" \
  /etc/systemd/system/cipher-browser-folder-sync.service
install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-folder-sync.timer" \
  /etc/systemd/system/cipher-browser-folder-sync.timer
install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-import.service" \
  /etc/systemd/system/cipher-browser-import.service
install -m 0644 \
  "$SYSTEMD_DIR/cipher-browser-import.timer" \
  /etc/systemd/system/cipher-browser-import.timer

"$PYTHON" -m compileall -q "$IMPORTER"
"$PYTHON" -m pytest -q "$ROOT/cipher-system/tests/test_browser_gcs_importer.py"

systemctl daemon-reload
systemctl enable --now cipher-browser-folder-sync.timer cipher-browser-import.timer
systemctl restart cipher-browser-folder-sync.timer cipher-browser-import.timer

# These one-shot services safely no-op outside their configured ET windows.
systemctl start cipher-browser-folder-sync.service
systemctl start cipher-browser-import.service

printf '%s\n' '--- timer status ---'
systemctl is-enabled cipher-browser-folder-sync.timer cipher-browser-import.timer
systemctl is-active cipher-browser-folder-sync.timer cipher-browser-import.timer

printf '%s\n' '--- current one-shot results ---'
systemctl status cipher-browser-folder-sync.service cipher-browser-import.service --no-pager --lines=12 || true
