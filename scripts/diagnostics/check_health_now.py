#!/usr/bin/env python3
"""Current health check with corrected market hours (13:30-20:00 UTC)."""
from datetime import datetime, timezone
import sqlite3
import json
from pathlib import Path

DATA = Path("/home/aarav/Aarav/cipher/cipher-system/data")
LIVE_OPTION_CHAINS_DIR = DATA / "live_option_chains"

SCANNER_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ",
)

# Correct market hours per skill: 13:30-20:00 UTC
MARKET_OPEN_UTC = 13.5
MARKET_CLOSE_UTC = 20.0

THRESHOLDS = {"tradier": 5, "gex": 15, "chains": 15}

now = datetime.now(timezone.utc)
hour_float = now.hour + now.minute / 60.0
market = MARKET_OPEN_UTC <= hour_float <= MARKET_CLOSE_UTC

print(f"Current time: {now.isoformat()} ({hour_float:.2f} UTC)")
print(f"Market hours (13:30-20:00 UTC): {market}")

def parse_dt(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def age_min(dt):
    return (now - dt).total_seconds() / 60.0

# --- Tradier ---
db = DATA / "tradier_stream.sqlite"
tradier_age = None
tradier_stale = False
if db.exists():
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT MAX(last_event_at) FROM tradier_stream_runs").fetchone()
    latest = parse_dt(row[0]) if row and row[0] else None
    if latest:
        tradier_age = age_min(latest)
        tradier_stale = market and tradier_age >= THRESHOLDS["tradier"]
        print(f"Tradier: {tradier_age:.1f} min old - {'STALE' if tradier_stale else 'OK'}")
    else:
        tradier_stale = market
        print("Tradier: NO DATA")
else:
    tradier_stale = market
    print("Tradier: DB MISSING")

# --- GEX ---
db = DATA / "gex_history.sqlite"
gex_age = None
gex_stale = False
if db.exists():
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT MAX(captured_at) FROM gex_snapshots").fetchone()
    latest = parse_dt(row[0]) if row and row[0] else None
    if latest:
        gex_age = age_min(latest)
        gex_stale = market and gex_age >= THRESHOLDS["gex"]
        print(f"GEX: {gex_age:.1f} min old - {'STALE' if gex_stale else 'OK'}")
    else:
        gex_stale = market
        print("GEX: NO DATA")
else:
    gex_stale = market
    print("GEX: DB MISSING")

# --- Live Option Chains (24/7) ---
stale_chains = []
missing_chains = []
if LIVE_OPTION_CHAINS_DIR.exists():
    for ticker in SCANNER_TICKERS:
        path = LIVE_OPTION_CHAINS_DIR / f"latest_{ticker}.json"
        if not path.is_file():
            missing_chains.append(ticker)
            stale_chains.append((ticker, float("inf")))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing_chains.append(ticker)
            stale_chains.append((ticker, float("inf")))
            continue
        ts = None
        for key in ("as_of", "timestamp", "fetched_at", "captured_at", "snapshot_time", "time"):
            if key in data:
                ts = data[key]
                break
        dt = parse_dt(ts)
        if not dt:
            missing_chains.append(ticker)
            stale_chains.append((ticker, float("inf")))
            continue
        age = age_min(dt)
        if age >= THRESHOLDS["chains"]:
            stale_chains.append((ticker, age))
    # Also show fresh ones
    for ticker in SCANNER_TICKERS:
        path = LIVE_OPTION_CHAINS_DIR / f"latest_{ticker}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ts = None
                for key in ("as_of", "timestamp", "fetched_at", "captured_at", "snapshot_time", "time"):
                    if key in data:
                        ts = data[key]
                        break
                dt = parse_dt(ts)
                if dt:
                    age = age_min(dt)
                    if age < THRESHOLDS["chains"] and ticker not in missing_chains:
                        print(f"  {ticker}: {age:.1f} min [OK]")
            except:
                pass
else:
    stale_chains = [(t, float("inf")) for t in SCANNER_TICKERS]
    missing_chains = list(SCANNER_TICKERS)

print(f"\nLive Option Chains (24/7):")
if stale_chains:
    for t, a in stale_chains:
        if a == float("inf"):
            print(f"  {t}: MISSING/INVALID")
        else:
            print(f"  {t}: {a:.1f} min [STALE]")
else:
    print("  All fresh")

# --- Alert determination ---
alert_needed = tradier_stale or gex_stale or bool(stale_chains)
print(f"\nAlert needed: {alert_needed}")
tradier_age_str = f"{tradier_age:.1f}" if tradier_age is not None else "N/A"
gex_age_str = f"{gex_age:.1f}" if gex_age is not None else "N/A"
print(f"  Tradier stale: {tradier_stale} (market={market}, age={tradier_age_str}, thresh={THRESHOLDS['tradier']})")
print(f"  GEX stale: {gex_stale} (market={market}, age={gex_age_str}, thresh={THRESHOLDS['gex']})")
print(f"  Chains stale: {bool(stale_chains)} ({len(stale_chains)} tickers)")

# Write result for cron
import sys
result = {
    "alert_needed": alert_needed,
    "market_hours": market,
    "tradier_age_min": tradier_age,
    "gex_age_min": gex_age,
    "stale_chains": [[t, a if a != float("inf") else None] for t, a in stale_chains],
    "missing_chains": missing_chains,
    "timestamp": now.isoformat(),
}
with open(DATA / "health_check_result.json", "w") as f:
    json.dump(result, f, indent=2)

sys.exit(1 if alert_needed else 0)