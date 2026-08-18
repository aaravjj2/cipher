import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

now = datetime.now(timezone.utc)
print(f"Current UTC time: {now.isoformat()}")

market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
market_close = now.replace(hour=22, minute=0, second=0, microsecond=0)
is_market_hours = market_open <= now <= market_close
print(f"Is market hours (13:30-22:00 UTC): {is_market_hours}")

# 1. Tradier stream - check latest quote timestamp
tradier_db = Path("cipher-system/data/tradier_stream.sqlite")
print(f"\n=== Tradier Stream ===")
tradier_stale = False
tradier_age_min = None
if tradier_db.exists():
    conn = sqlite3.connect(tradier_db)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(last_event_at) FROM tradier_stream_runs")
    result = cursor.fetchone()
    if result and result[0]:
        ts_str = result[0]
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        tradier_age_min = (now - dt).total_seconds() / 60
        print(f"  Latest run last_event_at: {dt.isoformat()} ({tradier_age_min:.1f} min ago)")
    
    if tradier_age_min is not None:
        if is_market_hours and tradier_age_min >= 5:
            tradier_stale = True
            print(f"  STALE (>=5 min during market hours)")
        elif not is_market_hours:
            print(f"  Off-hours (expected, no alert)")
    conn.close()
else:
    print("  FILE NOT FOUND")
    tradier_stale = True

# 2. GEX history
gex_db = Path("cipher-system/data/gex_history.sqlite")
print(f"\n=== GEX History ===")
gex_stale = False
gex_age_min = None
if gex_db.exists():
    conn = sqlite3.connect(gex_db)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(captured_at) FROM gex_snapshots")
    result = cursor.fetchone()
    if result and result[0]:
        ts_str = result[0]
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        gex_age_min = (now - dt).total_seconds() / 60
        print(f"  Latest gex_snapshots: {dt.isoformat()} ({gex_age_min:.1f} min ago)")
    conn.close()
    if gex_age_min is not None and gex_age_min >= 15:
        if is_market_hours:
            gex_stale = True
            print(f"  STALE (>=15 min during market hours)")
        else:
            print(f"  Stale but off-hours (expected, no alert)")
else:
    print("  FILE NOT FOUND")
    gex_stale = True

# 3. Live option chains (24/7 stream - always check)
chains_dir = Path("cipher-system/data/live_option_chains")
print(f"\n=== Live Option Chains (24/7 stream) ===")
expected = ["SPY", "QQQ", "IWM", "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD"]
stale_chains = []
fresh_chains = []
if chains_dir.exists():
    files = list(chains_dir.glob("latest_*.json"))
    print(f"Found {len(files)} latest_*.json files")
    for f in files:
        ticker = f.stem.replace("latest_", "")
        try:
            with open(f) as fp:
                data = json.load(fp)
            ts = None
            for key in ['timestamp', 'fetched_at', 'captured_at', 'snapshot_time', 'time']:
                if key in data:
                    ts = data[key]
                    break
            if ts:
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                age_min = (now - dt).total_seconds() / 60
                status = "FRESH" if age_min < 5 else "STALE"
                print(f"  {ticker}: {age_min:.1f} min old ({status}) - {dt.isoformat()}")
                if age_min >= 5:
                    stale_chains.append((ticker, age_min))
                else:
                    fresh_chains.append((ticker, age_min))
            else:
                print(f"  {ticker}: NO TIMESTAMP in data")
                stale_chains.append((ticker, float('inf')))
        except Exception as e:
            print(f"  {ticker}: ERROR reading - {e}")
            stale_chains.append((ticker, float('inf')))
    found = {f.stem.replace("latest_", "") for f in files}
    missing = [t for t in expected if t not in found]
    if missing:
        print(f"  MISSING tickers: {missing}")
        for t in missing:
            stale_chains.append((t, float('inf')))
else:
    print("  DIRECTORY NOT FOUND")
    stale_chains = [(t, float('inf')) for t in expected]

# Summary
print(f"\n=== SUMMARY ===")
print(f"Market hours: {is_market_hours}")
print(f"Tradier: {'STALE' if tradier_stale else 'OK'} ({tradier_age_min:.1f} min)" if tradier_age_min is not None else "Tradier: MISSING")
print(f"GEX: {'STALE' if gex_stale else 'OK'} ({gex_age_min:.1f} min)" if gex_age_min is not None else "GEX: MISSING")
print(f"Chains: {len(fresh_chains)} fresh, {len(stale_chains)} stale/missing")
for t, age in stale_chains:
    print(f"  {t}: {'MISSING/ERROR' if age == float('inf') else f'{age:.1f} min'}")

# Determine if alert needed
alert_needed = False
alert_reasons = []

# Tradier: only alert during market hours
if is_market_hours and tradier_stale:
    alert_needed = True
    alert_reasons.append(f"Tradier equity stream stale ({tradier_age_min:.1f} min)")

# GEX: only alert during market hours
if is_market_hours and gex_stale:
    alert_needed = True
    alert_reasons.append(f"GEX snapshots stale ({gex_age_min:.1f} min)")

# Live option chains: 24/7 stream - ALWAYS alert if stale
if stale_chains:
    alert_needed = True
    tickers = [t for t, _ in stale_chains]
    alert_reasons.append(f"Live option chains stale/missing (24/7): {', '.join(tickers)}")

print(f"\nALERT NEEDED: {alert_needed}")
if alert_reasons:
    for r in alert_reasons:
        print(f"  - {r}")

# Write result for telegram
result = {
    "alert_needed": alert_needed,
    "reasons": alert_reasons,
    "market_hours": is_market_hours,
    "tradier_age_min": tradier_age_min,
    "gex_age_min": gex_age_min,
    "stale_chains": [(t, a if a != float('inf') else None) for t, a in stale_chains],
    "fresh_chains": fresh_chains,
    "timestamp": now.isoformat()
}
with open("cipher-system/data/health_check_result.json", "w") as f:
    json.dump(result, f, indent=2)
print(f"\nResult written to health_check_result.json")