#!/usr/bin/env python3
"""Clean per-ticker MAX freshness check for GEX snapshots (scanner universe)."""
import sqlite3
from datetime import datetime, timezone

DB = "/home/aarav/Aarav/cipher/runtime/data/gex_history.sqlite"
TICKERS = ("NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL",
           "TSLA", "META", "MU", "AMD", "QQQ", "SPY", "IWM")

now = datetime.now(timezone.utc)
print(f"Current time: {now.isoformat()}")

with sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30) as db:
    rows = db.execute(
        "SELECT ticker, MAX(captured_at) FROM gex_snapshots "
        "WHERE ticker IN ({}) GROUP BY ticker ORDER BY ticker".format(
            ",".join("?" for _ in TICKERS)
        ),
        TICKERS,
    ).fetchall()

rows_by_ticker = dict(rows)
stale = []
for ticker in TICKERS:
    raw = rows_by_ticker.get(ticker)
    if not raw:
        print(f"  {ticker}: NO DATA")
        stale.append(ticker)
        continue
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    age = (now - dt).total_seconds() / 60
    state = "OK" if age <= 15 else "STALE"
    if state == "STALE":
        stale.append(ticker)
    print(f"  {ticker}: {dt.isoformat()} ({age:.1f} min) [{state}]")

print()
if stale:
    print(f"STALE: {stale}")
else:
    print("ALL FRESH")
