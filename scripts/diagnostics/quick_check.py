import sqlite3

conn = sqlite3.connect('/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM tradier_latest_quotes')
print('tradier_latest_quotes count:', cursor.fetchone()[0])
cursor.execute('SELECT symbol, updated_at FROM tradier_latest_quotes ORDER BY updated_at DESC LIMIT 5')
for row in cursor.fetchall():
    print(f'  {row}')
conn.close()