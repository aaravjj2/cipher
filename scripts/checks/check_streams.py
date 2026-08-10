#!/usr/bin/env python3
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('/home/aarav/Aarav/cipher/cipher-system/data')
now = datetime.now(timezone.utc)

# Check tradier
tradier_db = DATA / 'tradier_stream.sqlite'
with sqlite3.connect(tradier_db) as db:
    rows = db.execute('select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5').fetchall()
    latest_tradier = max((datetime.fromisoformat(row[1].replace('Z', '+00:00')).astimezone(timezone.utc) for row in rows), default=None)

# Check gex
gex_db = DATA / 'gex_history.sqlite'
with sqlite3.connect(gex_db) as db:
    rows = db.execute('select ticker, captured_at from gex_snapshots order by captured_at desc limit 5').fetchall()
    latest_gex = max((datetime.fromisoformat(row[1].replace('Z', '+00:00')).astimezone(timezone.utc) for row in rows), default=None)

# Check live option chains
live_chains_dir = DATA / 'live_option_chains'
tickers = ['AAPL', 'AMD', 'AMZN', 'AVGO', 'GOOGL', 'IBIT', 'META', 'MSFT', 'MU', 'NVDA', 'QQQ', 'TSLA']
live_chain_times = {}
for t in tickers:
    f = live_chains_dir / f'latest_{t}.json'
    if f.exists():
        result = subprocess.run(['jq', '-r', '.timestamp', str(f)], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            live_chain_times[t] = datetime.fromisoformat(result.stdout.strip().replace('Z', '+00:00')).astimezone(timezone.utc)

latest_live = max(live_chain_times.values()) if live_chain_times else None
oldest_live = min(live_chain_times.values()) if live_chain_times else None

print(f'Current time: {now.isoformat()}')
print(f'Tradier latest: {latest_tradier.isoformat() if latest_tradier else "NONE"} (age: {(now - latest_tradier).total_seconds()/60:.1f} min)' if latest_tradier else 'Tradier: NONE')
print(f'GEX latest: {latest_gex.isoformat() if latest_gex else "NONE"} (age: {(now - latest_gex).total_seconds()/60:.1f} min)' if latest_gex else 'GEX: NONE')
print(f'Live chains latest: {latest_live.isoformat() if latest_live else "NONE"} (age: {(now - latest_live).total_seconds()/60:.1f} min)' if latest_live else 'Live chains: NONE')
print(f'Live chains oldest: {oldest_live.isoformat() if oldest_live else "NONE"} (age: {(now - oldest_live).total_seconds()/60:.1f} min)' if oldest_live else 'Live chains oldest: NONE')
for t, dt in sorted(live_chain_times.items()):
    age = (now - dt).total_seconds() / 60
    print(f'  {t}: {dt.isoformat()} (age: {age:.1f} min)')

# Check thresholds
alerts = []
if latest_tradier:
    tradier_age = (now - latest_tradier).total_seconds() / 60
    if tradier_age > 5:
        alerts.append(f"Tradier equity stream STALE: {tradier_age:.1f} min old (threshold <5 min)")
else:
    alerts.append("Tradier equity stream: NO DATA")

if latest_gex:
    gex_age = (now - latest_gex).total_seconds() / 60
    if gex_age > 15:
        alerts.append(f"Alpaca GEX snapshots STALE: {gex_age:.1f} min old (threshold <15 min)")
else:
    alerts.append("Alpaca GEX snapshots: NO DATA")

if live_chain_times:
    for t, dt in live_chain_times.items():
        age = (now - dt).total_seconds() / 60
        if age > 15:
            alerts.append(f"Live option chain {t} STALE: {age:.1f} min old (threshold <15 min)")
else:
    alerts.append("Live option chains: NO DATA")

if alerts:
    print("\n=== ALERTS ===")
    for a in alerts:
        print(a)
else:
    print("\n=== ALL STREAMS HEALTHY ===")