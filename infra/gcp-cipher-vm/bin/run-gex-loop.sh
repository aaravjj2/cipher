#!/usr/bin/env bash
# ── Cipher GEX capture loop ─────────────────────────────────────────────
# Conservative: runs only during market hours, limits tickers on first
# pass to avoid Alpaca 429 storms.  Set GEX_SMOKE_LIMIT=0 for full run.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher/cipher-system"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
INTERVAL_SECONDS="${GEX_CAPTURE_INTERVAL_SECONDS:-900}"
SMOKE_LIMIT="${GEX_SMOKE_LIMIT:-5}"        # first-pass limit; 0 = unlimited
TIERS="${GEX_CAPTURE_TIERS:-mega,large,medium}"
EXPIRATIONS="${GEX_CAPTURE_EXPIRATIONS:-1}"
SLEEP_MS="${GEX_CAPTURE_SLEEP_MS:-1500}"

pass_count=0

while true; do
  weekday="$(TZ=America/New_York date +%u)"
  market_time="$(TZ=America/New_York date +%H%M)"
  if [[ "$weekday" -le 5 && "$market_time" -ge 0930 && "$market_time" -le 1605 ]]; then
    pass_count=$((pass_count + 1))

    # Use smoke limit on first 2 passes, then full run
    limit_args=()
    if [[ "$SMOKE_LIMIT" != "0" && "$pass_count" -le 2 ]]; then
      limit_args=(--limit "$SMOKE_LIMIT")
    fi

    "$PYTHON" "$ROOT/core/gex_capture.py" \
      --all \
      --tiers "$TIERS" \
      --expirations "$EXPIRATIONS" \
      --sleep-ms "$SLEEP_MS" \
      "${limit_args[@]}" || true

    sleep "$INTERVAL_SECONDS"
  else
    sleep 60
  fi
done
