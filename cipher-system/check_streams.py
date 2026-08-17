#!/usr/bin/env python3
import sqlite3
import json
from datetime import datetime, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/aarav/Aarav/cipher/cipher-system")
DATA = Path("/home/aarav/Aarav/cipher/runtime/data")
LIVE_OPTION_CHAINS_DIR = DATA / "live_option_chains"
NY = ZoneInfo("America/New_York")

SCANNER_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ",
)

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

def market_window(kind):
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return False
    current = now.time()
    if kind == "tradier":
        return time(7, 30) <= current <= time(17, 0)
    return time(9, 30) <= current <= time(16, 10)

# Check Tradier
print("=== TRADIER ===")
tradier_active = market_window("tradier")
print(f"Market active (tradier): {tradier_active}")

db_path = DATA / "tradier_stream.sqlite"
print(f"Tradier DB size: {db_path.stat().st_size / (1024**3):.2f} GB")
with sqlite3.connect(db_path) as db:
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print('Tables:', tables)

    count = db.execute('SELECT count(*) FROM tradier_stream_events').fetchone()[0]
    print('tradier_stream_events count:', count)

    row = db.execute('SELECT MAX(updated_at) FROM tradier_stream_events').fetchone()
    latest = parse_dt(row[0]) if row[0] else None
    print('Latest updated_at in tradier_stream_events:', latest)

    # Also check tradier_latest_quotes
    try:
        count2 = db.execute('SELECT count(*) FROM tradier_latest_quotes').fetchone()[0]
        print('tradier_latest_quotes count:', count2)
        row2 = db.execute('SELECT MAX(updated_at) FROM tradier_latest_quotes').fetchone()
        latest2 = parse_dt(row2[0]) if row2[0] else None
        print('Latest updated_at in tradier_latest_quotes:', latest2)
        if latest and latest2:
            latest = max(latest, latest2)
        elif latest2:
            latest = latest2
    except Exception as e:
        print('tradier_latest_quotes error:', e)

if latest:
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    print(f"Age: {age:.1f} minutes")
    if tradier_active and age > 5:
        print("ALERT: Tradier stream is STALE (>5 min during market hours)")
    elif not tradier_active and age > 60:
        print("INFO: Tradier stream is old (outside market hours)")

# Check GEX
print("\n=== GEX ===")
gex_active = market_window("gex")
print(f"Market active (gex): {gex_active}")

db_path = DATA / "gex_history.sqlite"
print(f"GEX DB size: {db_path.stat().st_size / (1024**2):.2f} MB")
with sqlite3.connect(db_path) as db:
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print('Tables:', tables)

    count = db.execute('SELECT count(*) FROM gex_snapshots').fetchone()[0]
    print('gex_snapshots count:', count)

    row = db.execute('SELECT MAX(captured_at) FROM gex_snapshots').fetchone()
    latest = parse_dt(row[0]) if row[0] else None
    print('Latest captured_at:', latest)

if latest:
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    print(f"Age: {age:.1f} minutes")
    if gex_active and age > 15:
        print("ALERT: GEX snapshots are STALE (>15 min during market hours)")
    elif not gex_active and age > 60:
        print("INFO: GEX snapshots are old (outside market hours)")

# Check Live Option Chains
print("\n=== LIVE OPTION CHAINS ===")
option_chains_active = market_window("gex")
print(f"Market active (option_chains): {option_chains_active}")

per_ticker = {}
for ticker in SCANNER_TICKERS:
    latest_path = LIVE_OPTION_CHAINS_DIR / f"latest_{ticker}.json"
    if not latest_path.is_file():
        print(f"  {ticker}: MISSING")
        continue
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  {ticker}: ERROR - {e}")
        continue
    observed = parse_dt(payload.get("as_of") or payload.get("timestamp"))
    if observed is not None:
        per_ticker[ticker] = observed
        age = (datetime.now(timezone.utc) - observed).total_seconds() / 60
        print(f"  {ticker}: {observed} (age: {age:.1f} min)")
    else:
        print(f"  {ticker}: NO TIMESTAMP")

latest = max(per_ticker.values(), default=None)
if latest:
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    print(f"Overall latest: {latest} (age: {age:.1f} min)")

missing = [ticker for ticker in SCANNER_TICKERS if ticker not in per_ticker]
if missing:
    print(f"Missing tickers: {missing}")
    if option_chains_active:
        print("ALERT: Some tickers missing during market hours")
else:
    if option_chains_active and age > 5:
        print("ALERT: Option chains are STALE (>5 min during market hours)")
    elif not option_chains_active and age > 60:
        print("INFO: Option chains are old (outside market hours)")
