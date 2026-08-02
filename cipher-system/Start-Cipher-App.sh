#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHON="${PYTHON:-python3}"
export PORT="${PORT:-8283}"
export CIPHER_CORE_PORT="${CIPHER_CORE_PORT:-8282}"
export CIPHER_CORE_URL="${CIPHER_CORE_URL:-http://127.0.0.1:${CIPHER_CORE_PORT}}"

mkdir -p /tmp
echo "Starting Cipher Core on :${CIPHER_CORE_PORT} …"
"${PYTHON}" -u "$ROOT/core/app.py" > /tmp/cipher-core.log 2>&1 &
CORE_PID=$!
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${CIPHER_CORE_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

# Route smoke — warn if core is up but missing newer API routes (stale process / old code).
CORE="http://127.0.0.1:${CIPHER_CORE_PORT}"
smoke_warn() {
  local path="$1"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "${CORE}${path}" || echo "000")
  if [[ "$code" == "404" ]]; then
    echo "WARNING: ${path} returned 404 — stale core? Restart after pulling latest code." >&2
  elif [[ "$code" != "200" ]]; then
    echo "WARNING: ${path} returned HTTP ${code} (expected 200)." >&2
  else
    echo "OK ${path}"
  fi
}
echo "Core route smoke…"
smoke_warn "/api/health"
smoke_warn "/api/weight-lab?action=status"
smoke_warn "/api/backtest?action=list"

echo "Starting Cipher Research app on :${PORT} …"
node "$ROOT/app/server.mjs" > /tmp/cipher-web.log 2>&1 &
WEB_PID=$!
sleep 0.4
echo "Open http://127.0.0.1:${PORT}/"
echo "Logs: /tmp/cipher-core.log  /tmp/cipher-web.log"
echo "PIDs: core=${CORE_PID} web=${WEB_PID}"
echo "Stop with: kill ${CORE_PID} ${WEB_PID}"
wait
