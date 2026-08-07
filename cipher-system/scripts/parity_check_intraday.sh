#!/usr/bin/env bash
# parity_check_intraday.sh — capture the real product and diff it against ours,
# during market hours, and record the result.
#
# Why intraday specifically. Same-day (0DTE) cells are the only part of the grid
# still materially off, and they cannot be judged after the bell: once 16:00 ET
# passes, a same-day contract has effectively expired and BOTH sides are
# extrapolating a degenerate quantity. Every 0DTE parity number gathered so far was
# measured after the close, which is why they are recorded as provisional.
#
# Runs at 11:00 and 14:30 ET so the same-day column is live and quoted, and the two
# samples bracket the session rather than pinning it to one moment.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="$ROOT/logs/parity_check.log"
LOCK="/tmp/cipher_parity_check.lock"
PYTHON="${PYTHON:-python3}"
SYMBOLS="${SYMBOLS:-NVDA,AAPL,SPY,COIN,PLTR,AVGO,NFLX,UBER,XOM,MU}"

mkdir -p "$ROOT/logs"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >> "$LOG"; }

exec 9>"$LOCK"
if ! flock -n 9; then
    log "skip: another parity check holds the lock"
    exit 0
fi

# The capture drives a real browser through Kimi WebBridge. If the daemon is not
# up there is nothing to capture, and failing quietly here is better than a
# half-written report that looks like a parity regression.
if ! curl -s -m 5 -o /dev/null "http://127.0.0.1:10086/command"; then
    log "skip: Kimi WebBridge not reachable"
    exit 0
fi

if ! curl -s -m 10 -o /dev/null "http://127.0.0.1:8283/api/health"; then
    log "skip: local Cipher stack not running"
    exit 0
fi

log "capture start ($SYMBOLS)"
cd "$ROOT/.." || exit 1
if ! "$PYTHON" cipher-system/scripts/capture_ticker_views.py --symbols "$SYMBOLS" >> "$LOG" 2>&1; then
    log "capture FAILED"
    exit 1
fi

cd "$ROOT" || exit 1
log "comparing"
"$PYTHON" scripts/compare_ticker_views.py >> "$LOG" 2>&1 && log "compare ok" || log "compare FAILED"
