#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher/cipher-system"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
UNDERLYINGS="${TRADIER_STREAM_SYMBOLS:-SPY,QQQ,IWM,NVDA,MSFT,AAPL,AVGO,AMZN,IBIT,GOOGL,TSLA,META,MU,AMD}"
OPTION_UNDERLYINGS="${TRADIER_OPTION_UNDERLYINGS:-$UNDERLYINGS}"
REFRESH_SECONDS="${TRADIER_STREAM_REFRESH_SECONDS:-900}"
MAX_RUN_SECONDS="${TRADIER_STREAM_DURATION_SECONDS:-840}"
LOOP_LOCK="$ROOT/data/tradier_stream_loop.lock"

# Exactly one scheduler may run. This is separate from the per-capture Python
# lock so an accidentally launched manual wrapper cannot compete with systemd.
exec 9>"$LOOP_LOCK"
if ! flock -n 9; then
  printf '{"skipped":true,"reason":"another Tradier scheduler holds %s"}\n' "$LOOP_LOCK"
  exit 0
fi

while true; do
  weekday="$(TZ=America/New_York date +%u)"
  today="$(TZ=America/New_York date +%F)"
  now_epoch="$(date +%s)"
  open_epoch="$(TZ=America/New_York date -d "$today 09:30:00" +%s)"
  close_epoch="$(TZ=America/New_York date -d "$today 16:00:00" +%s)"

  if [[ "$weekday" -gt 5 || "$now_epoch" -lt "$open_epoch" || "$now_epoch" -ge "$close_epoch" ]]; then
    sleep 60
    continue
  fi

  remaining=$((close_epoch - now_epoch))
  duration="$MAX_RUN_SECONDS"
  if (( duration > remaining )); then
    duration="$remaining"
  fi
  if (( duration < 20 )); then
    sleep "$remaining"
    continue
  fi

  "$PYTHON" "$ROOT/core/tradier_stream_capture.py" \
    --symbols "$UNDERLYINGS" \
    --option-underlyings "$OPTION_UNDERLYINGS" \
    --duration-seconds "$duration" \
    --option-expirations "${TRADIER_OPTION_EXPIRATIONS:-1}" \
    --option-strikes-per-side "${TRADIER_OPTION_STRIKES_PER_SIDE:-2}" \
    --option-min-dte "${TRADIER_OPTION_MIN_DTE:-0}" \
    --option-max-dte "${TRADIER_OPTION_MAX_DTE:-14}" \
    --max-options-per-underlying "${TRADIER_MAX_OPTIONS_PER_UNDERLYING:-8}" \
    --max-stream-symbols "${TRADIER_MAX_STREAM_SYMBOLS:-160}" || true

  # Five seconds is enough to rotate the Tradier session and refresh the option
  # universe. The capture duration controls the normal 15-minute cadence.
  sleep 5

done
