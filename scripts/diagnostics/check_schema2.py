#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

# Check tradier_stream.sqlite - tradier_latest_quotes table
conn = sqlite3.connect('cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()

# Check provider_ts
cursor.execute('SELECT MAX(provider_ts) FROM tradier_latest_quotes WHERE provider_ts IS NOT NULL')
max_ts = cursor.fetchone()[0]
print(f"MAX provider_ts: {max_ts}")

# Check updated_at
cursor.execute('SELECT MAX(updated_at) FROM tradier_latest_quotes WHERE updated_at IS NOT NULL')
max_ts2 = cursor.fetchone()[0]
print(f"MAX updated_at: {max_ts2}")

# Sample some rows
cursor.execute('SELECT symbol, provider_ts, updated_at FROM tradier_latest_quotes LIMIT 5')
for row in cursor.fetchall():
    print(f"  {row}")

conn.close()

# Check gex_history.sqlite schema
conn = sqlite3.connect('cipher-system/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(gex_snapshots)")
for row in cursor.fetchall():
    print(row)
conn.close()