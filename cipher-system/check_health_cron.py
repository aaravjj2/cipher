#!/usr/bin/env python3
"""Cipher data health check with market-hours awareness and Telegram alerting."""

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import subprocess

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

def is_market_hours(now: datetime) -> bool:
    """Check if current time is within US market hours (9:30-16:00 ET, Mon-Fri)."""
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    hour_utc = now.hour + now.minute / 60
    return 13.5 <= hour_utc <= 21.0

# 1. Tradier
tradier_db = DATA / 'tradier_stream.sqlite'
with sqlite3.connect(tradier_db) as db:
    rows = db.execute('select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5').fetchall()
    tradier_event_count = db.execute('select coalesce(max(id), 0) from tradier_stream_events').fetchone()[0]
tradier_latest = None
for row in rows:
    dt = parse_dt(row[1])
    if dt and (tradier_latest is None or dt > tradier_latest):
        tradier_latest = dt

# 2. GEX
gex_db = DATA / 'gex_history.sqlite'
with sqlite3.connect(gex_db) as db:
    rows = db.execute('select ticker, captured_at from gex_snapshots order by captured_at desc limit 5').fetchall()
    gex_snapshot_count = db.execute('select count(*) from gex_snapshots').fetchone()[0]
gex_latest = None
for row in rows:
    dt = parse_dt(row[1])
    if dt and (gex_latest is None or dt > gex_latest):
        gex_latest = dt

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
market_open = is_market_hours(now)

def age_min(latest):
    if latest is None:
        return None
    return (now - latest).total_seconds() / 60

tradier_age = age_min(tradier_latest)
gex_age = age_min(gex_latest)
live_age = age_min(live_latest)

alerts = []

# Live option chains: 24/7 stream, 15 min threshold always
if live_latest is None or (live_age is not None and live_age > 15):
    alerts.append({
        'stream': 'LIVE_OPTION_CHAINS',
        'status': 'STALE',
        'age': live_age,
        'threshold': 15,
        'details': f'all {len(per_ticker)}/12 tickers ~{live_age:.0f} min old' if live_latest else 'no data'
    })

# Tradier: 5 min threshold, market hours only
if market_open:
    if tradier_latest is None or (tradier_age is not None and tradier_age > 5):
        alerts.append({
            'stream': 'TRADIER',
            'status': 'STALE',
            'age': tradier_age,
            'threshold': 5,
            'details': f'~{tradier_age:.0f} min old' if tradier_latest else 'no data'
        })

# GEX: 15 min threshold, market hours only
if market_open:
    if gex_latest is None or (gex_age is not None and gex_age > 15):
        alerts.append({
            'stream': 'GEX',
            'status': 'STALE',
            'age': gex_age,
            'threshold': 15,
            'details': f'~{gex_age:.0f} min old' if gex_latest else 'no data'
        })

# Print summary
print(f"Current time: {now.isoformat()}")
print(f"Market hours: {'YES' if market_open else 'NO'}")
print()
print(f"TRADIER: latest={tradier_latest.isoformat() if tradier_latest else 'None'}")
print(f"  Age: {tradier_age:.1f} min" if tradier_age else "  No data")
print(f"  Events: {tradier_event_count}")
print()
print(f"GEX: latest={gex_latest.isoformat() if gex_latest else 'None'}")
print(f"  Age: {gex_age:.1f} min" if gex_age else "  No data")
print(f"  Snapshots: {gex_snapshot_count}")
print()
print(f"LIVE_OPTION_CHAINS: latest={live_latest.isoformat() if live_latest else 'None'}")
print(f"  Age: {live_age:.1f} min" if live_age else "  No data")
print(f"  Per-ticker ages:")
for ticker in SCANNER_TICKERS:
    if ticker in per_ticker:
        age = (now - per_ticker[ticker]).total_seconds() / 60
        print(f"    {ticker}: {age:.1f} min")
    else:
        print(f"    {ticker}: MISSING")
print(f"  Missing: {[t for t in SCANNER_TICKERS if t not in per_ticker]}")
print()

if alerts:
    print("ALERTS TRIGGERED:")
    for a in alerts:
        print(f"  {a['stream']}: {a['status']} - {a['details']} (threshold {a['threshold']} min)")
else:
    print("All streams healthy")

# Build alert message if needed
if alerts:
    lines = ["Cipher data health change"]
    lines.append(f"Checked: {now.isoformat()} ({'market open' if market_open else 'market closed'})")
    lines.append("")

    # Live option chains (always checked)
    if live_latest:
        lines.append(f"LIVE_OPTION_CHAINS: STALE - {len(per_ticker)}/12 tickers ~{live_age:.0f} min old (threshold 15 min, 24/7 stream)")
        # Show per-ticker ages
        tickers_sorted = sorted(per_ticker.items(), key=lambda x: x[1])
        for i, (t, dt) in enumerate(tickers_sorted):
            age = (now - dt).total_seconds() / 60
            prefix = "  " if i % 4 == 0 else ", "
            if i > 0 and i % 4 == 0:
                lines.append("")
            lines[-1] += f"{prefix}{t}: {age:.1f} min"
        lines.append(f"Last update: ~{live_latest.isoformat()}")
    else:
        lines.append("LIVE_OPTION_CHAINS: STALE - no data")

    # Tradier
    if market_open:
        if tradier_latest:
            status = "STALE" if tradier_age and tradier_age > 5 else "OK"
            lines.append(f"TRADIER: {status} - {tradier_age:.0f} min old (threshold 5 min, market hours)")
        else:
            lines.append("TRADIER: STALE - no data")
    else:
        lines.append(f"TRADIER: OK - {tradier_age:.0f} min old (expected, outside market hours)")

    # GEX
    if market_open:
        if gex_latest:
            status = "STALE" if gex_age and gex_age > 15 else "OK"
            lines.append(f"GEX: {status} - {gex_age:.0f} min old (threshold 15 min, market hours)")
        else:
            lines.append("GEX: STALE - no data")
    else:
        lines.append(f"GEX: OK - {gex_age:.0f} min old (expected, outside market hours)")

    lines.append(f"Current time: {now.strftime('%H:%M UTC')} ({'market open' if market_open else 'market closed'})")

    message = "\n".join(lines)
    print("\n--- Alert Message ---")
    print(message)
    print("--- End Message ---\n")

    # Send via Hermes
    try:
        result = subprocess.run(
            ['hermes', 'send', '--to', 'telegram', message],
            capture_output=True, text=True, timeout=60
        )
        print(f"Hermes return code: {result.returncode}")
        print(f"Hermes stdout: {result.stdout}")
        if result.stderr:
            print(f"Hermes stderr: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("Hermes timed out")
    except FileNotFoundError:
        print("Hermes CLI not found in PATH")
else:
    print("No alerts needed - all streams within thresholds for current market state")
