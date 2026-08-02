#!/usr/bin/env bash
# capture_gex_loop.sh — Automated GEX snapshot capture loop for Cipher.
#
# Captures GEX matrix snapshots at regular intervals during market hours.
# Uses the Python gex_capture module with Alpaca-backed data.
#
# Usage:
#   ./cipher-system/scripts/capture_gex_loop.sh [OPTIONS]
#
# Options:
#   --ticker SYM       Capture single ticker (repeatable)
#   --all              Capture full universe (mega+large+medium)
#   --interval MIN     Minutes between captures (default: 15)
#   --feed FEED        Options feed: opra|indicative (default: opra)
#   --depth DEPTH      Strike depth: 0.06|all|percent (default: 0.06)
#   --expirations N    Number of expiration columns (default: 1)
#   --limit N          Max tickers per pass (default: 0 = no limit)
#   --sleep-ms MS      Delay between tickers in ms (default: 1250)
#   --market-hours     Only run during market hours (9:30-16:00 ET)
#   --once             Run one pass and exit
#   --help             Show this help
#
# Examples:
#   ./cipher-system/scripts/capture_gex_loop.sh --all --interval 15
#   ./cipher-system/scripts/capture_gex_loop.sh --ticker SPY --ticker QQQ --once
#   ./cipher-system/scripts/capture_gex_loop.sh --all --market-hours --interval 30

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "$SCRIPT_DIR/../core" && pwd)"

# Defaults
MODE=""
TICKERS=()
INTERVAL=15
FEED="opra"
DEPTH="0.06"
EXPIRATIONS=1
LIMIT=0
SLEEP_MS=1250
MARKET_HOURS=false
ONCE=false

usage() {
    head -30 "$0" | grep '^#' | sed 's/^# \?//'
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ticker)
            MODE="--ticker"
            TICKERS+=("$2")
            shift 2
            ;;
        --all)
            MODE="--all"
            shift
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --feed)
            FEED="$2"
            shift 2
            ;;
        --depth)
            DEPTH="$2"
            shift 2
            ;;
        --expirations)
            EXPIRATIONS="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --sleep-ms)
            SLEEP_MS="$2"
            shift 2
            ;;
        --market-hours)
            MARKET_HOURS=true
            shift
            ;;
        --once)
            ONCE=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "$MODE" ]]; then
    echo "Error: must specify --ticker SYM or --all"
    usage
fi

# Check Python
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    echo "Error: python3 not found. Set PYTHON env var."
    exit 1
fi

# Check that core is importable
if ! "$PYTHON" -c "import sys; sys.path.insert(0, '$CORE_DIR'); import gex_capture" 2>/dev/null; then
    echo "Warning: gex_capture module not importable. Ensure dependencies are installed."
fi

is_market_hours() {
    # Check if current time is between 9:30-16:00 ET, Mon-Fri
    local hour minute dow
    hour=$(TZ="America/New_York" date +%H)
    minute=$(TZ="America/New_York" date +%M)
    dow=$(TZ="America/New_York" date +%u)  # 1=Mon, 7=Sun

    # Weekend check
    if [[ "$dow" -ge 6 ]]; then
        return 1
    fi

    local time_val=$((hour * 60 + minute))
    # 9:30 = 570, 16:00 = 960
    if [[ "$time_val" -ge 570 && "$time_val" -le 960 ]]; then
        return 0
    fi
    return 1
}

run_capture() {
    local args=()
    args+=("--feed" "$FEED")
    args+=("--depth" "$DEPTH")
    args+=("--expirations" "$EXPIRATIONS")
    args+=("--sleep-ms" "$SLEEP_MS")

    if [[ "$LIMIT" -gt 0 ]]; then
        args+=("--limit" "$LIMIT")
    fi

    if [[ "$MODE" == "--all" ]]; then
        args+=("--all")
    else
        for t in "${TICKERS[@]}"; do
            args+=("--ticker" "$t")
        done
    fi

    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Starting capture pass..."
    cd "$CORE_DIR"
    "$PYTHON" gex_capture.py "${args[@]}"
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Capture pass complete."
}

# Main loop
echo "=== Cipher GEX Capture Loop ==="
echo "Mode: $MODE"
echo "Feed: $FEED | Depth: $DEPTH | Expirations: $EXPIRATIONS"
echo "Interval: ${INTERVAL}m | Sleep between tickers: ${SLEEP_MS}ms"
echo "Market hours only: $MARKET_HOURS"
echo ""

if [[ "$ONCE" == "true" ]]; then
    run_capture
    exit 0
fi

while true; do
    if [[ "$MARKET_HOURS" == "true" ]]; then
        if ! is_market_hours; then
            echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Outside market hours. Sleeping 5 minutes..."
            sleep 300
            continue
        fi
    fi

    run_capture

    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Sleeping ${INTERVAL} minutes..."
    sleep $((INTERVAL * 60))
done
