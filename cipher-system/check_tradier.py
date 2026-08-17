#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

db_path = '/home/aarav/Aarav/cipher/runtime/data/tradier_stream.sqlite'
print(f"Tradier DB size: {__import__('os').path.getsize(db_path) / (1024**3):.2f} GB")
with sqlite3.connect(db_path) as db:
    # Check tradier_latest_quotes table
    try:
        count = db.execute('SELECT count(*) FROM tradier_latest_quotes').fetchone()[0]
        print('tradier_latest_quotes count:', count)
        row = db.execute('SELECT MAX(updated_at) FROM tradier_latest_quotes').fetchone()
        latest = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00')).astimezone(timezone.utc) if row[0] else None
        print('Latest updated_at in tradier_latest_quotes:', latest)
        if latest:
            age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
            print(f'Age: {age:.1f} minutes')
    except Exception as e:
        print('tradier_latest_quotes error:', e)

    # Check tradier_stream_events table (might be large)
    try:
        count = db.execute('SELECT count(*) FROM tradier_stream_events').fetchone()[0]
        print('tradier_stream_events count:', count)
        row = db.execute('SELECT MAX(updated_at) FROM tradier_stream_events').fetchone()
        latest = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00')).astimezone(timezone.utc) if row[0] else None
        print('Latest updated_at in tradier_stream_events:', latest)
        if latest:
            age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
            print(f'Age: {age:.1f} minutes')
    except Exception as e:
        print('tradier_stream_events error:', e)
