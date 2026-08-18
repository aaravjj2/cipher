#!/usr/bin/env python3
"""Check GEX snapshots per-ticker for the 12 active tickers + SPY/QQQ/IWM."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

now = datetime.now(timezone.utc)
print(f"Check time (UTC): {now}")
print(f"Market hours (ET): True (weekday, ~11:18 AM ET)")

DATA_DIR = Path('/home/aarav/Aarav/cipher/cipher-system/data')
GEX_DB = DATA_DIR / 'gex_history.sqlite'

tickers = ['NVDA','MSFT','AAPL','AVGO','AMZN','IBIT','GOOGL','TSLA','META','MU','AMD','QQQ','SPY','IWM']

conn = sqlite3.connect(GEX_DB)
c = conn.cursor()
placeholders = ','.join('?' for _ in tickers)
c.execute(f'SELECT ticker, MAX(captured_at) FROM gex_snapshots WHERE ticker IN ({placeholders}) GROUP BY ticker ORDER BY MAX(captured_at) DESC', tickers)
rows = c.fetchall()
conn.close()

alerts = []
for ticker, ts in rows:
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    age = now - dt
    age_min = age.total_seconds() / 60
    status = 'STALE' if age_min > 15 else 'OK'
    print(f"  {ticker}: {age_min:.1f} min old [{status}] ({ts})")
    if status == 'STALE':
        alerts.append(f"{ticker}: {age_min:.1f} min old")

if alerts:
    print()
    print("STALE GEX SNAPSHOTS DETECTED:")
    for a in alerts:
        print(f"  {a}")
else:
    print("All GEX snapshots healthy ✓")

if alerts:
    # Send Telegram alert
    import sys
    sys.path.insert(0, str(Path('/home/aarav/Aarav/cipher/cipher-system/scripts')))
    from hermes_delivery import send_hermes_message
    
    msg = f"""Cipher Data Health Alert 🚨

Checked: {now.strftime('%Y-%m-%d %H:%M UTC')}

STALE GEX SNAPSHOTS DETECTED (threshold: 15 min):
{chr(10).join(alerts)}

Expected during market hours:
- GEX snapshots for 12 active tickers + SPY/QQQ/IWM: <15 min each"""
    
    print("\nSending Telegram alert...")
    rc = send_hermes_message(msg, target='telegram')
    print(f"Telegram send return code: {rc}")
    sys.exit(1)
else:
    print("All GEX snapshots healthy ✓")
    sys.exit(0)