#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher"
APP="$ROOT/cipher-system"
INFRA="$ROOT/infra/gcp-cipher-vm"
PYTHON="/home/aarav/.venvs/cipher/bin/python"

if [[ ! -f "$APP/core/tradier_stream_capture.py" ]]; then
  echo "ERROR: Tradier collector not found under $APP" >&2
  exit 1
fi

printf '%s\n' 'Stopping old Tradier collectors...'
sudo systemctl stop cipher-tradier.service 2>/dev/null || true
pkill -TERM -f '^bash /home/aarav/Aarav/cipher/infra/gcp-cipher-vm/bin/run-tradier-loop\.sh$' 2>/dev/null || true
pkill -TERM -f '^/bin/bash -lic set \+m; cd /home/aarav/Aarav/cipher && /home/aarav/Aarav/cipher/infra/gcp-cipher-vm/bin/run-tradier-loop\.sh$' 2>/dev/null || true
sleep 2

printf '%s\n' 'Installing hardened scheduler and systemd unit...'
sudo install -d -m 0755 /usr/local/lib/cipher /etc/systemd/system
sudo install -m 0755 "$INFRA/bin/run-tradier-loop.sh" /usr/local/lib/cipher/run-tradier-loop.sh
sudo install -m 0644 "$INFRA/systemd/cipher-tradier.service" /etc/systemd/system/cipher-tradier.service

printf '%s\n' 'Migrating schema and repairing historical run counters...'
"$PYTHON" "$APP/core/tradier_stream_capture.py" --maintenance-only

printf '%s\n' 'Resolving the full bounded option universe...'
"$PYTHON" "$APP/core/tradier_stream_capture.py" --resolve-only \
  | jq '{resolved_symbol_count,option_contract_count,error_count:(.errors|length),errors}'

printf '%s\n' 'Running an isolated 20-second option-stream smoke test...'
SMOKE_DIR="$(mktemp -d /tmp/cipher-tradier-smoke.XXXXXX)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
"$PYTHON" "$APP/core/tradier_stream_capture.py" \
  --symbols SPY,NVDA \
  --option-underlyings SPY,NVDA \
  --option-expirations 1 \
  --option-strikes-per-side 1 \
  --max-options-per-underlying 4 \
  --max-stream-symbols 10 \
  --duration-seconds 20 \
  --allow-outside-session \
  --db "$SMOKE_DIR/stream.sqlite" \
  --raw-dir "$SMOKE_DIR/events" \
  --selection-output "$SMOKE_DIR/selection.json" \
  --lock-path "$SMOKE_DIR/stream.lock"
"$PYTHON" "$APP/scripts/check_tradier_stream_health.py" \
  --db "$SMOKE_DIR/stream.sqlite" --ignore-age

printf '%s\n' 'Starting the managed collector...'
sudo systemctl daemon-reload
sudo systemctl enable cipher-tradier.service
sudo systemctl restart cipher-tradier.service
sleep 3
sudo systemctl status cipher-tradier.service --no-pager

printf '%s\n' 'Tradier fix deployed. The scheduler records only 09:30-16:00 America/New_York.'
