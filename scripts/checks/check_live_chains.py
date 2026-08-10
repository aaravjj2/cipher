#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone

data_dir = Path('cipher-system/data/live_option_chains')
tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']
now = datetime.now(timezone.utc)

for ticker in tickers:
    latest_file = data_dir / f'latest_{ticker}.json'
    if latest_file.exists():
        import os
        mtime = datetime.fromtimestamp(os.path.getmtime(latest_file), tz=timezone.utc)
        age_min = (now - mtime).total_seconds() / 60
        try:
            with open(latest_file) as f:
                content = json.load(f)
                content_ts = content.get('snapshot_ts') or content.get('captured_at') or content.get('timestamp')
        except:
            content_ts = None
        print(f'{ticker}: file mtime {mtime.isoformat()} ({age_min:.1f} min ago)')
    else:
        print(f'{ticker}: NO latest_ file found')