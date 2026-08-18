import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

# Check tradier schema
conn = sqlite3.connect('cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(tradier_latest_quotes)")
print("tradier_latest_quotes columns:")
for row in cursor.fetchall():
    print(f"  {row}")

cursor.execute("PRAGMA table_info(tradier_stream_events)")
print("\ntradier_stream_events columns:")
for row in cursor.fetchall():
    print(f"  {row}")

cursor.execute("PRAGMA table_info(tradier_stream_runs)")
print("\ntradier_stream_runs columns:")
for row in cursor.fetchall():
    print(f"  {row}")
conn.close()

# Check gex schema
conn = sqlite3.connect('cipher-system/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"\nGEX tables: {tables}")
for table in tables:
    t = table[0]
    cursor.execute(f"PRAGMA table_info({t})")
    cols = cursor.fetchall()
    print(f"\n  {t} columns:")
    for c in cols:
        print(f"    {c}")
conn.close()