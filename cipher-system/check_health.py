#!/usr/bin/env python3
import sqlite3
import json
from datetime import datetime, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/aarav/Aarav/cipher/cipher-system")
DATA = ROOT / "data"
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
    print(f"Current NY time: {now}")
    if now.weekday() >= 5:
        print(f"Weekend (weekday={now.weekday()}), market closed")
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
with sqlite3.connect(db_path) as db:
    rows = db.execute(
        "select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5"
    ).fetchall()
    count = db.execute("select count(*) from tradier_stream_events").fetchone()[0]

latest = max((parse_dt(row[1]) for row in rows), default=None)
print(f"Latest quote: {latest}")
print(f"Events count: {count}")
print(f"Top 5 rows: {rows}")

if latest:
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    print(f"Age: {age:.1f} minutes")

# Check GEX
print("")
print("=== GEX ===")
gex_active = market_window("gex")
print(f"Market active (gex): {gex_active}")

db_path = DATA / "gex_history.sqlite"
with sqlite3.connect(db_path) as db:
    rows = db.execute(
        "select ticker, captured_at from gex_snapshots order by captured_at desc limit 5"
    ).fetchall()
    count = db.execute("select count(*) from gex_snapshots").fetchone()[0]

latest = max((parse_dt(row[1]) for row in rows), default=None)
print(f"Latest snapshot: {latest}")
print(f"Snapshots count: {count}")
print(f"Top 5 rows: {rows}")

if latest:
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    print(f"Age: {age:.1f} minutes")

# Check Live Option Chains
print("")
print("=== LIVE OPTION CHAINS ===")
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
else:
    print("No valid timestamps found")

missing = [ticker for ticker in SCANNER_TICKERS if ticker not in per_ticker]
if missing:
    print(f"Missing tickers: {missing}")