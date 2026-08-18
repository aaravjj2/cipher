#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/home/aarav/Aarav/cipher/runtime/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cursor.fetchall()
print('Tables:', tables)
for table in tables:
    cursor.execute(f'PRAGMA table_info({table[0]})')
    cols = cursor.fetchall()
    print(f'Table {table[0]} columns:', [c[1] for c in cols])
    # Check for timestamp columns
    time_cols = [c[1] for c in cols if 'time' in c[1].lower() or 'ts' in c[1].lower() or 'timestamp' in c[1].lower() or 'date' in c[1].lower()]
    if time_cols:
        cursor.execute(f'SELECT MAX({time_cols[0]}) FROM {table[0]}')
        max_ts = cursor.fetchone()[0]
        print(f'  MAX {time_cols[0]}: {max_ts}')
        # Try to get count of recent rows
        cursor.execute(f"SELECT COUNT(*) FROM {table[0]} WHERE {time_cols[0]} > datetime('now', '-1 hour')")
        recent = cursor.fetchone()[0]
        print(f'  Recent (last hour): {recent}')

conn.close()