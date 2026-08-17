#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

db_path = '/home/aarav/Aarav/cipher/runtime/data/gex_history.sqlite'
with sqlite3.connect(db_path) as db:
    row = db.execute('SELECT MAX(captured_at) FROM gex_snapshots').fetchone()
    print('Latest GEX captured_at:', row[0])
    if row[0]:
        dt = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00')).astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        print(f'Age: {age:.1f} minutes')
