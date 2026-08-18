#!/usr/bin/env python3
import sqlite3
from pathlib import Path

# Check Tradier
db_path = Path('/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite')
with sqlite3.connect(db_path) as db:
    rows = db.execute('select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5').fetchall()
    count = db.execute('select coalesce(max(id), 0) from tradier_stream_events').fetchone()[0]
    print('TRADIER:')
    for row in rows:
        print(f'  {row[0]}: {row[1]}')
    print(f'  Events: {count}')

# Check GEX
db_path = Path('/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite')
with sqlite3.connect(db_path) as db:
    rows = db.execute('select ticker, captured_at from gex_snapshots order by captured_at desc limit 5').fetchall()
    count = db.execute('select count(*) from gex_snapshots').fetchone()[0]
    print('GEX:')
    for row in rows:
        print(f'  {row[0]}: {row[1]}')
    print(f'  Snapshots: {count}')