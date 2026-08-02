import sqlite3
db = sqlite3.connect('/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite')
row = db.execute('SELECT MAX(captured_at) FROM tradier_stream_events').fetchone()
print('Latest event captured_at:', row[0])
row = db.execute('SELECT id, started_at, completed_at, event_count, stop_reason FROM tradier_stream_runs ORDER BY id DESC LIMIT 1').fetchone()
print('Latest run:', row)
row = db.execute('SELECT symbol, updated_at FROM tradier_latest_quotes ORDER BY updated_at DESC LIMIT 5').fetchall()
print('Latest quotes:', row)