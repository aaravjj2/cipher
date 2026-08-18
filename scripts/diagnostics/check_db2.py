import sqlite3
import os

path1 = '/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite'
path2 = '/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite'

for path in [path1, path2]:
    print(f'=== {os.path.basename(path)} ===')
    print(f'Size: {os.path.getsize(path)} bytes')
    print(f'Modified: {os.path.getmtime(path)}')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
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
                cursor.execute(f'SELECT * FROM {t} LIMIT 3;')
                rows = cursor.fetchall()
                print(f'  Sample: {rows}')
                # Get column names
                cursor.execute(f'PRAGMA table_info({t});')
                cols = cursor.fetchall()
                print(f'  Columns: {[c[1] for c in cols]}')
        conn.close()
    except Exception as e:
        print(f'Error: {e}')