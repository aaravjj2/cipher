#!/usr/bin/env python3
import sqlite3
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
base = Path("/home/aarav/Aarav/cipher/cipher-system")

alerts = []

# 1. Check Tradier equity stream (tradier_stream.sqlite)
tradier_db = base / "data" / "tradier_stream.sqlite"
if tradier_db.exists():
    conn = sqlite3.connect(tradier_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"\nTradier DB tables: {tables}")
    
    for table in tables:
        table_name = table[0]
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = cursor.fetchall()
        print(f"  Table {table_name} columns: {[c[1] for c in cols]}")
        
        ts_cols = [c[1] for c in cols if 'time' in c[1].lower() or 'date' in c[1].lower() or 'ts' in c[1].lower()]
        if ts_cols:
            for ts_col in ts_cols:
                try:
                    cursor.execute(f"SELECT MAX({ts_col}) FROM {table_name}")
                    max_ts = cursor.fetchone()[0]
                    if max_ts:
                        if isinstance(max_ts, (int, float)):
                            if max_ts > 1e12:
                                ts_dt = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc)
                            else:
                                ts_dt = datetime.fromtimestamp(max_ts, tz=timezone.utc)
                        else:
                            ts_dt = datetime.fromisoformat(str(max_ts).replace('Z', '+00:00'))
                        age = (now - ts_dt).total_seconds() / 60
                        print(f"  {table_name}.{ts_col}: {ts_dt.isoformat()} (age: {age:.1f} min)")
                        if is_market_hours and age > 5:
                            alerts.append(f"Tradier stream stale: {age:.1f} min old (table={table_name}, col={ts_col})")
                except Exception as e:
                    print(f"  Error reading {table_name}.{ts_col}: {e}")
    conn.close()
else:
    print(f"\nTradier DB not found: {tradier_db}")
    if is_market_hours:
        alerts.append("Tradier stream DB not found")

# 2. Check Alpaca GEX snapshots (gex_history.sqlite)
gex_db = base / "data" / "gex_history.sqlite"
if gex_db.exists():
    conn = sqlite3.connect(gex_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"\nGEX DB tables: {tables}")
    
    for table in tables:
        table_name = table[0]
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = cursor.fetchall()
        print(f"  Table {table_name} columns: {[c[1] for c in cols]}")
        
        ts_cols = [c[1] for c in cols if 'time' in c[1].lower() or 'date' in c[1].lower() or 'ts' in c[1].lower() or 'captured' in c[1].lower()]
        if ts_cols:
            for ts_col in ts_cols:
                try:
                    cursor.execute(f"SELECT MAX({ts_col}) FROM {table_name}")
                    max_ts = cursor.fetchone()[0]
                    if max_ts:
                        if isinstance(max_ts, (int, float)):
                            if max_ts > 1e12:
                                ts_dt = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc)
                            else:
                                ts_dt = datetime.fromtimestamp(max_ts, tz=timezone.utc)
                        else:
                            ts_dt = datetime.fromisoformat(str(max_ts).replace('Z', '+00:00'))
                        age = (now - ts_dt).total_seconds() / 60
                        print(f"  {table_name}.{ts_col}: {ts_dt.isoformat()} (age: {age:.1f} min)")
                        if age > 15:
                            alerts.append(f"GEX snapshots stale: {age:.1f} min old (table={table_name}, col={ts_col})")
                except Exception as e:
                    print(f"  Error reading {table_name}.{ts_col}: {e}")
    conn.close()
else:
    print(f"\nGEX DB not found: {gex_db}")
    alerts.append("GEX history DB not found")

# 3. Check Live option chains (live_option_chains/)
live_chains_dir = base / "data" / "live_option_chains"
if live_chains_dir.exists():
    print(f"\nLive option chains dir: {live_chains_dir}")
    tickers = ["NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"]
    stale_tickers = []
    
    for ticker in tickers:
        ticker_dir = live_chains_dir / ticker
        if ticker_dir.exists():
            files = list(ticker_dir.glob("*.json"))
            if files:
                latest_file = max(files, key=lambda f: f.stat().st_mtime)
                mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
                age = (now - mtime).total_seconds() / 60
                print(f"  {ticker}: {latest_file.name} (age: {age:.1f} min)")
                if age > 15:
                    stale_tickers.append(f"{ticker} ({age:.1f} min)")
            else:
                print(f"  {ticker}: NO FILES")
                stale_tickers.append(f"{ticker} (no files)")
        else:
            print(f"  {ticker}: NO DIR")
            stale_tickers.append(f"{ticker} (no dir)")
    
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