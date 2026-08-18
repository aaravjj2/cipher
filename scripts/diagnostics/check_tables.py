#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
for t in tables:
    print(t[0])
conn.close()