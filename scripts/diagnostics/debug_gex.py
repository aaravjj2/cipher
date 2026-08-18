#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/home/aarav/Aarav/cipher/runtime/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cursor.fetchall()
print('Tables:', tables)
for table in tables:
    cursor.execute(f'PRAGMA table_info({table[0]})')
    cols = cursor.fetchall()
    print(f'Table {table[0]} columns:', [c[1] for c in cols])
    cursor.execute(f'SELECT * FROM {table[0]} LIMIT 3')
    rows = cursor.fetchall()
    for row in rows:
        print(f'  Row: {row}')
conn.close()