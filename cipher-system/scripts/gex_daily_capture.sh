#!/usr/bin/env bash
# gex_daily_capture.sh — one universe-wide GEX snapshot per trading day, from cron.
#
# Why this exists: GEX is gamma x open interest, so backtesting cluster or GEX
# strategies needs point-in-time OI. Replaying today's OI over past prices is
# lookahead bias and will manufacture edge that does not exist. The only fix is
# to accrue real daily snapshots going forward.
#
# The previous approach was a hand-started foreground loop, which died with the
# shell every time — data/gex_history.sqlite has a 2026-07-31..08-07 gap for
# exactly that reason. Cron plus flock survives logout, reboot and overlap.
#
# Runs at 15:00 ET, not nearer the close: a full 546-ticker pass takes ~27 minutes,
# so a 15:50 start would put the tail of the snapshot after the bell and mix pre-
# and post-close spot into a single capture. Skips non-trading days by asking
# Alpaca's clock rather than guessing holidays.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/gex_capture.log"
LOCK="/tmp/cipher_gex_capture.lock"
PYTHON="${PYTHON:-python3}"

mkdir -p "$LOG_DIR"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >> "$LOG"; }

# flock -n: if yesterday's pass somehow still runs, skip rather than stack two
# full-universe scans and burn the API rate limit.
exec 9>"$LOCK"
if ! flock -n 9; then
    log "skip: another capture holds the lock"
    exit 0
fi

# Holiday/half-day guard. Alpaca knows the real calendar; a weekday check does not.
if [[ "${FORCE:-0}" != "1" ]]; then
    IS_OPEN="$(CIPHER_ROOT="$ROOT" "$PYTHON" - <<'PY' 2>/dev/null
import os, json, urllib.request
from pathlib import Path
try:
    root = Path(os.environ["CIPHER_ROOT"])
    env = {}
    for p in (root / ".env", root.parent / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    key = env.get("ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY")
    sec = env.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    req = urllib.request.Request(
        "https://api.alpaca.markets/v2/clock",
        headers={"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": sec or ""},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        print("1" if json.load(r).get("is_open") else "0")
except Exception:
    print("unknown")
PY
)"
    if [[ "$IS_OPEN" == "0" ]]; then
        log "skip: market closed today"
        exit 0
    fi
    # "unknown" falls through and captures — a redundant snapshot costs an API
    # pass, a missed one costs a day of history that cannot be recovered later.
fi

log "capture start"
cd "$ROOT/core" || exit 1
if "$PYTHON" gex_capture.py --all --feed opra --depth 0.06 --expirations 1 --sleep-ms 1250 >> "$LOG" 2>&1; then
    log "capture ok"
else
    log "capture FAILED rc=$?"
fi

# Keep the log from growing without bound.
if [[ -f "$LOG" ]] && [[ "$(stat -c%s "$LOG")" -gt 20000000 ]]; then
    tail -c 5000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
