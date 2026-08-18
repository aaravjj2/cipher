#!/usr/bin/env python3
"""
Cipher Stream Health Check with market-hours awareness.
Only alerts on actionable staleness:
- During market hours: all three streams should be fresh
- During off-hours: only live option chains (24/7 stream) should be fresh
"""
import sqlite3
import os
import glob
import json
import subprocess
from datetime import datetime, timezone, time as dtime
from pathlib import Path

now = datetime.now(timezone.utc)
print(f"Check time (UTC): {now}")
print(f"Day of week: {now.strftime('%A')}")

# Determine market hours
# US Market hours: 13:30-20:00 UTC (9:30 AM - 4:00 PM ET) on weekdays
# Pre-market: 08:00-13:30 UTC
# After-hours: 20:00-00:00 UTC
# Weekends: CLOSED
is_weekday = now.weekday() < 5  # Mon-Fri
current_time = now.timetz()

market_open = dtime(13, 30, tzinfo=timezone.utc)
market_close = dtime(20, 0, tzinfo=timezone.utc)
pre_market_open = dtime(8, 0, tzinfo=timezone.utc)
after_hours_close = dtime(0, 0, tzinfo=timezone.utc)  # midnight

in_market_hours = is_weekday and market_open <= current_time < market_close
in_pre_market = is_weekday and pre_market_open <= current_time < market_open
in_after_hours = is_weekday and (market_close <= current_time or current_time < after_hours_close)
is_weekend = not is_weekday

print(f"Weekday: {is_weekday}")
print(f"In market hours: {in_market_hours}")
print(f"In pre-market: {in_pre_market}")
print(f"In after-hours: {in_after_hours}")
print(f"Weekend: {is_weekend}")

# Data directory
DATA_DIR = Path('/home/aarav/Aarav/cipher/cipher-system/data')

alerts = []
info_lines = []

# ============================================================
# 1. Tradier Equity Stream (tradier_latest_quotes)
# ============================================================
conn = sqlite3.connect(DATA_DIR / 'tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT MAX(updated_at) FROM tradier_latest_quotes WHERE updated_at IS NOT NULL')
max_ts = cursor.fetchone()[0]
conn.close()

if max_ts:
    dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
    age_min = (now - dt).total_seconds() / 60
    info_lines.append(f"Tradier equity stream: latest quote at {dt} UTC (age: {age_min:.1f} min)")
    if in_market_hours and age_min >= 5:
        alerts.append(f"Tradier equity stream STALE during market hours: {age_min:.1f} min old (last: {dt} UTC)")
    elif not in_market_hours:
        info_lines[-1] += " [off-hours, expected stale]"
else:
    info_lines.append("Tradier equity stream: NO DATA")
    if in_market_hours:
        alerts.append("Tradier equity stream: NO DATA during market hours")

# ============================================================
# 2. GEX Snapshots (gex_history.sqlite)
# ============================================================
conn = sqlite3.connect(DATA_DIR / 'gex_history.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT MAX(captured_at) FROM gex_snapshots WHERE captured_at IS NOT NULL')
max_ts = cursor.fetchone()[0]
conn.close()

if max_ts:
    dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
    age_min = (now - dt).total_seconds() / 60
    info_lines.append(f"GEX history: latest snapshot at {dt} UTC (age: {age_min:.1f} min)")
    if in_market_hours and age_min >= 15:
        alerts.append(f"GEX history STALE during market hours: {age_min:.1f} min old (last: {dt} UTC)")
    elif not in_market_hours:
        info_lines[-1] += " [off-hours, expected stale]"
else:
    info_lines.append("GEX history: NO DATA")
    if in_market_hours:
        alerts.append("GEX history: NO DATA during market hours")

# ============================================================
# 3. Live Option Chains (24/7 stream - ALWAYS actionable)
# ============================================================
chain_dir = DATA_DIR / 'live_option_chains'
expected_tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']

files = glob.glob(str(chain_dir / 'latest_*.json'))

if files:
    stale_tickers = []
    missing_tickers = []
    per_ticker_info = []
    
    for ticker in expected_tickers:
        fpath = chain_dir / f'latest_{ticker}.json'
        if fpath.exists():
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            age_min = (now - mtime).total_seconds() / 60
            status = "OK" if age_min < 15 else "STALE"
            per_ticker_info.append(f"    {ticker}: {age_min:.1f} min [{status}]")
            if age_min >= 15:
                stale_tickers.append(f"{ticker} ({age_min:.1f} min)")
        else:
            per_ticker_info.append(f"    {ticker}: MISSING")
            missing_tickers.append(ticker)
    
    info_lines.append("Live Option Chains (24/7 stream):")
    info_lines.extend(per_ticker_info)
    
    if stale_tickers:
        alerts.append(f"Live option chains STALE tickers (24/7 stream): {', '.join(stale_tickers)}")
    if missing_tickers:
        alerts.append(f"Live option chains MISSING tickers: {', '.join(missing_tickers)}")
else:
    info_lines.append("Live option chains: NO FILES")
    alerts.append("Live option chains: NO FILES (24/7 stream)")

# ============================================================
# Print summary
# ============================================================
print("=" * 60)
print("CIPHER STREAM HEALTH CHECK")
print("=" * 60)
for line in info_lines:
    print(line)
print("=" * 60)

if alerts:
    print("ACTIONABLE ALERTS:")
    for a in alerts:
        print(f"  - {a}")
    
    # Send Telegram alert via hermes
    msg = f"""🚨 Cipher Stream Health Alert

Checked: {now.strftime('%Y-%m-%d %H:%M UTC')}
Market Status: {'OPEN (market hours)' if in_market_hours else 'CLOSED (off-hours/weekend)'}

Actionable Issues Detected:
{chr(10).join('• ' + a for a in alerts)}

Note: Tradier & GEX staleness is expected during off-hours.
Live option chains are a 24/7 stream - staleness is always actionable."""
    
    print("\nSending Telegram alert via hermes...")
    result = subprocess.run(['hermes', 'send', '--to', 'telegram', msg], capture_output=True, text=True, timeout=30)
    print(f"Return code: {result.returncode}")
    if result.stdout:
        print(f"stdout: {result.stdout}")
    if result.stderr:
        print(f"stderr: {result.stderr}")
else:
    print("All actionable streams healthy ✓")

print("=" * 60)