import sqlite3
import os
from datetime import datetime, timezone

path2 = '/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite'

conn = sqlite3.connect(f'file:{path2}?mode=ro', uri=True)
cursor = conn.cursor()

# Get max captured_at from gex_snapshots
cursor.execute('SELECT MAX(captured_at) FROM gex_snapshots;')
max_captured = cursor.fetchone()[0]
print(f'MAX(captured_at) from gex_snapshots: {max_captured}')

if max_captured:
    dt = datetime.fromisoformat(max_captured.replace('Z', '+00:00'))
    print(f'  UTC: {dt}')
    now = datetime.now(timezone.utc)
    age_min = (now - dt).total_seconds() / 60
    print(f'  Age: {age_min:.1f} minutes')

# Get per-ticker latest
cursor.execute('''
    SELECT ticker, MAX(captured_at) as latest
    FROM gex_snapshots
    GROUP BY ticker
    ORDER BY latest DESC
''')
print('\nPer-ticker latest:')
for row in cursor.fetchall():
    ticker, ts = row
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00')) if ts else None
    age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60 if dt else None
    print(f'  {ticker}: {dt} ({age_min:.1f} min ago)' if dt else f'  {ticker}: None')

conn.close()