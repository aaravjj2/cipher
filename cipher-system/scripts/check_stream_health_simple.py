#!/usr/bin/env python3
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Current time
now = datetime.now(timezone.utc)
print(f"Current UTC time: {now.isoformat()}")

# Check if market hours (9:30-16:00 ET = 13:30-20:00 UTC)
et_now = now.astimezone(timezone(timedelta(hours=-4)))  # EDT
market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
is_market_hours = market_open <= et_now <= market_close and et_now.weekday() < 5
print(f"ET time: {et_now.isoformat()}")
print(f"Market hours: {is_market_hours}")

# Base path
base = Path("/home/aarav/Aarav/cipher/cipher-github/cipher-system/data")

alerts = []

# 1. Check Tradier equity stream (tradier_stream.sqlite) - check file mtime
tradier_db = base / "tradier_stream.sqlite"
if tradier_db.exists():
    mtime = datetime.fromtimestamp(tradier_db.stat().st_mtime, tz=timezone.utc)
    age = (now - mtime).total_seconds() / 60
    print(f"\nTradier stream DB mtime: {mtime.isoformat()} (age: {age:.1f} min)")
    if is_market_hours and age > 5:
        alerts.append(f"Tradier stream stale: {age:.1f} min old (file mtime)")
else:
    print(f"\nTradier DB not found: {tradier_db}")
    if is_market_hours:
        alerts.append("Tradier stream DB not found")

# 2. Check Alpaca GEX snapshots (gex_history.sqlite) - check file mtime
gex_db = base / "gex_history.sqlite"
if gex_db.exists():
    mtime = datetime.fromtimestamp(gex_db.stat().st_mtime, tz=timezone.utc)
    age = (now - mtime).total_seconds() / 60
    print(f"GEX history DB mtime: {mtime.isoformat()} (age: {age:.1f} min)")
    if age > 15:
        alerts.append(f"GEX snapshots stale: {age:.1f} min old (file mtime)")
else:
    print(f"GEX DB not found: {gex_db}")
    alerts.append("GEX history DB not found")

# 3. Check Live option chains (live_option_chains/) - check latest_*.json files
live_chains_dir = base / "live_option_chains"
if live_chains_dir.exists():
    print(f"\nLive option chains dir: {live_chains_dir}")
    tickers = ["NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"]
    stale_tickers = []
    
    for ticker in tickers:
        latest_file = live_chains_dir / f"latest_{ticker}.json"
        if latest_file.exists():
            mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
            age = (now - mtime).total_seconds() / 60
            print(f"  {ticker}: {latest_file.name} (age: {age:.1f} min)")
            if age > 15:
                stale_tickers.append(f"{ticker} ({age:.1f} min)")
        else:
            print(f"  {ticker}: NO latest_{ticker}.json")
            stale_tickers.append(f"{ticker} (no file)")
    
    if stale_tickers:
        alerts.append(f"Live option chains stale: {', '.join(stale_tickers)}")
else:
    print(f"\nLive option chains dir not found: {live_chains_dir}")
    alerts.append("Live option chains directory not found")

print(f"\n=== ALERTS ({len(alerts)}) ===")
for a in alerts:
    print(f"  - {a}")

# Output for potential Telegram
result = {
    "timestamp": now.isoformat(),
    "market_hours": is_market_hours,
    "alerts": alerts,
    "alert_count": len(alerts)
}
print(f"\nRESULT: {json.dumps(result)}")