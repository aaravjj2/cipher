import sqlite3
import os

path2 = '/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite'

print(f'=== {os.path.basename(path2)} ===')
print(f'Size: {os.path.getsize(path2)} bytes')
print(f'Modified: {os.path.getmtime(path2)}')
try:
    conn = sqlite3.connect(f'file:{path2}?mode=ro', uri=True)
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM sqlite_master WHERE type="table";')
    tables = cursor.fetchall()
    print(f'Tables: {tables}')
    for table in tables:
        t = table[0]
        cursor.execute(f'SELECT COUNT(*) FROM {t};')
        count = cursor.fetchone()[0]
        print(f'  {t}: {count} rows')
        if count > 0:
            cursor.execute(f'PRAGMA table_info({t});')
            cols = cursor.fetchall()
            print(f'  Columns: {[c[1] for c in cols]}')
            # Get max timestamp
            for col in ['ts', 'timestamp', 'time', 'created_at']:
                if col in [c[1] for c in cols]:
                    cursor.execute(f'SELECT MAX({col}) FROM {t};')
                    max_ts = cursor.fetchone()[0]
                    print(f'  MAX({col}): {max_ts}')
                    break
    conn.close()
except Exception as e:
    print(f'Error: {e}')