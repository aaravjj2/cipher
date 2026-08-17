#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

LIVE_OPTION_CHAINS_DIR = Path("/home/aarav/Aarav/cipher/runtime/data/live_option_chains")

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
