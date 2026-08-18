import sqlite3
from datetime import datetime, timezone

# Check GEX history
conn = sqlite3.connect('/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT ticker, captured_at FROM gex_snapshots ORDER BY captured_at DESC LIMIT 5')
for row in cursor.fetchall():
    print(f'GEX: {row}')
cursor.execute('SELECT COUNT(*) FROM gex_snapshots')
print('GEX snapshots count:', cursor.fetchone()[0])
conn.close()

# Check live option chains
import json
import os
chain_dir = '/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains'
tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']
print('\nLive option chains:')
for ticker in tickers:
    latest_path = os.path.join(chain_dir, f'latest_{ticker}.json')
    if os.path.exists(latest_path):
        try:
            with open(latest_path, 'r') as f:
                data = json.load(f)
            as_of = data.get('as_of') or data.get('timestamp')
            if as_of:
                print(f'  {ticker}: {as_of}')
            else:
                print(f'  {ticker}: no as_of/timestamp field')
        except Exception as e:
            print(f'  {ticker}: error - {e}')
    else:
        print(f'  {ticker}: NO FILE')