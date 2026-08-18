import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

now = datetime.now(timezone.utc)
print(f"Current UTC time: {now.isoformat()}")

# 1. Check Tradier equity stream
tradier_db = Path('/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite')
if tradier_db.exists():
    conn = sqlite3.connect(tradier_db)
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(updated_at) FROM tradier_latest_quotes')
    max_ts = cursor.fetchone()[0]
    conn.close()
    if max_ts:
        max_dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
        age = now - max_dt
        print(f"Tradier equity stream: last update {max_dt.isoformat()} (age: {age.total_seconds()/60:.1f} min)")
    else:
        print("Tradier equity stream: NO DATA")
else:
    print("Tradier equity stream: DATABASE NOT FOUND")

# 2. Check GEX history
gex_db = Path('/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite')
if gex_db.exists():
    conn = sqlite3.connect(gex_db)
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(captured_at) FROM gex_snapshots')
    max_ts = cursor.fetchone()[0]
    conn.close()
    if max_ts:
        max_dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
        age = now - max_dt
        print(f"GEX snapshots: last update {max_dt.isoformat()} (age: {age.total_seconds()/60:.1f} min)")
    else:
        print("GEX snapshots: NO DATA")
else:
    print("GEX snapshots: DATABASE NOT FOUND")

# 3. Check live option chains
chains_dir = Path('/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains')
if chains_dir.exists():
    tickers = ['SPY', 'QQQ', 'IWM', 'NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD']
    stale = []
    for ticker in tickers:
        files = list(chains_dir.glob(f'latest_{ticker}.json'))
        if files:
            f = files[0]
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            if age.total_seconds() > 300:
                stale.append((ticker, age.total_seconds()/60))
            print(f"  {ticker}: {mtime.isoformat()} (age: {age.total_seconds()/60:.1f} min)")
        else:
            stale.append((ticker, None))
            print(f"  {ticker}: NO FILE")
    if stale:
        print(f"Stale/missing tickers: {stale}")
else:
    print("Live option chains: DIRECTORY NOT FOUND")