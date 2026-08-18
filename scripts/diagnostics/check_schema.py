#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(tradier_latest_quotes)")
for row in cursor.fetchall():
    print(row)
conn.close()