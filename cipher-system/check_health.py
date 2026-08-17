#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

DATA = Path('/home/aarav/Aarav/cipher/cipher-system/data')
LIVE_OPTION_CHAINS_DIR = DATA / 'live_option_chains'
SCANNER_TICKERS = ('NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ')

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None

# 1. Tradier
tradier_db = DATA / 'tradier_stream.sqlite'
with sqlite3.connect(tradier_db) as db:
    rows = db.execute('select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5').fetchall()
    count = db.execute('select coalesce(max(id), 0) from tradier_stream_events').fetchone()[0]
tradier_latest = max((parse_dt(row[1]) for row in rows), default=None)

# 2. GEX
gex_db = DATA / 'gex_history.sqlite'
with sqlite3.connect(gex_db) as db:
    rows = db.execute('select ticker, captured_at from gex_snapshots order by captured_at desc limit 5').fetchall()
    count = db.execute('select count(*) from gex_snapshots').fetchone()[0]
gex_latest = max((parse_dt(row[1]) for row in rows), default=None)

# 3. Live option chains
per_ticker = {}
for ticker in SCANNER_TICKERS:
    latest_path = LIVE_OPTION_CHAINS_DIR / f'latest_{ticker}.json'
    if not latest_path.is_file():
        continue
    try:
        payload = json.loads(latest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        continue
    observed = parse_dt(payload.get('as_of') or payload.get('timestamp'))
    if observed is not None:
        per_ticker[ticker] = observed
live_latest = max(per_ticker.values(), default=None)

now = datetime.now(timezone.utc)
print(f'Current time: {now.isoformat()}')
print()

# Tradier: 20 min threshold, market hours only
print(f'TRADIER: latest={tradier_latest.isoformat() if tradier_latest else "None"}')
print(f'  Age: {(now - tradier_latest).total_seconds() / 60:.1f} min' if tradier_latest else '  No data')
print(f'  Events: {count}')
print()

# GEX: 45 min threshold, market hours only
print(f'GEX: latest={gex_latest.isoformat() if gex_latest else "None"}')
print(f'  Age: {(now - gex_latest).total_seconds() / 60:.1f} min' if gex_latest else '  No data')
print(f'  Snapshots: {count}')
print()

# Live option chains: 15 min threshold, 24/7
print(f'LIVE_OPTION_CHAINS: latest={live_latest.isoformat() if live_latest else "None"}')
print(f'  Age: {(now - live_latest).total_seconds() / 60:.1f} min' if live_latest else '  No data')
print(f'  Per-ticker ages:')
for ticker in SCANNER_TICKERS:
    if ticker in per_ticker:
        age = (now - per_ticker[ticker]).total_seconds() / 60
        print(f'    {ticker}: {age:.1f} min')
    else:
        print(f'    {ticker}: MISSING')
print(f'  Missing: {[t for t in SCANNER_TICKERS if t not in per_ticker]}')
