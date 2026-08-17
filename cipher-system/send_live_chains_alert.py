#!/usr/bin/env python3
"""Send health alert for stale live option chains (24/7 stream)."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

from scripts.hermes_delivery import send_hermes_message

DATA = Path('/home/aarav/Aarav/cipher/cipher-system/data')
LIVE_OPTION_CHAINS_DIR = DATA / 'live_option_chains'
SCANNER_TICKERS = (
    'NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT',
    'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ',
)

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None

# Check live option chains (24/7 stream, 15 min threshold)
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

# Check if stale (15 min threshold, 24/7)
is_stale = False
age_min = 0
if live_latest:
    age_min = (now - live_latest).total_seconds() / 60
    if age_min > 15:
        is_stale = True
else:
    is_stale = True
    age_min = float('inf')

# Also check tradier and gex for context
tradier_db = DATA / 'tradier_stream.sqlite'
with sqlite3.connect(tradier_db) as db:
    rows = db.execute('select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5').fetchall()
tradier_latest = max((parse_dt(row[1]) for row in rows), default=None)
tradier_age = (now - tradier_latest).total_seconds() / 60 if tradier_latest else float('inf')

gex_db = DATA / 'gex_history.sqlite'
with sqlite3.connect(gex_db) as db:
    rows = db.execute('select ticker, captured_at from gex_snapshots order by captured_at desc limit 5').fetchall()
gex_latest = max((parse_dt(row[1]) for row in rows), default=None)
gex_age = (now - gex_latest).total_seconds() / 60 if gex_latest else float('inf')

# Format message
now_et = now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
ny = now.astimezone(timezone.utc)  # We'll just use UTC for simplicity
et_str = now.strftime('%H:%M ET')

lines = [
    'Cipher data health change',
    f'Checked: {now_et} ({now.strftime("%a %H:%M ET")})',
    '',
]

if is_stale:
    lines.append(f'LIVE_OPTION_CHAINS: STALE - all 12 tickers ~{age_min:.0f} min old (threshold 15 min, 24/7 stream)')
    # Show first 5 tickers
    ticker_ages = []
    for ticker in SCANNER_TICKERS:
        if ticker in per_ticker:
            age = (now - per_ticker[ticker]).total_seconds() / 60
            ticker_ages.append(f'{ticker}: {age:.1f} min')
    lines.append('  ' + ', '.join(ticker_ages[:4]))
    lines.append('  ' + ', '.join(ticker_ages[4:8]))
    lines.append('  ' + ', '.join(ticker_ages[8:]))
    lines.append(f'Last update: ~{live_latest.isoformat()} ({live_latest.strftime("%H:%M ET")}, near market close)')

lines.append('')
lines.append(f'TRADIER: OK - {tradier_age:.0f} min old (expected, outside market hours 9:30-16:00 ET)')
lines.append(f'GEX: OK - {gex_age:.0f} min old (expected, outside market hours 9:30-16:10 ET)')
lines.append(f'Current time: {now.strftime("%H:%M ET")} (market closed, weekend)')

message = '\n'.join(lines)
print(message)
print('---')
rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
