import json
from pathlib import Path

chain_dir = Path('/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains')
tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ', 'SPY', 'IWM']

for ticker in tickers:
    latest_path = chain_dir / f'latest_{ticker}.json'
    if not latest_path.is_file():
        print(f'{ticker}: MISSING')
        continue
    try:
        payload = json.loads(latest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        print(f'{ticker}: INVALID JSON')
        continue
    timestamp = payload.get('as_of') or payload.get('timestamp') or 'NO_TIMESTAMP'
    print(f'{ticker}: {timestamp}')