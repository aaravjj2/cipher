import urllib.request
import json
from datetime import datetime, timezone

tickers = ["NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"]

results = {}
for ticker in tickers:
    try:
        url = f"http://localhost:8282/api/matrix?symbol={ticker}&expirations=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'cipher-health-check'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            as_of = data.get('as_of', 'N/A')
            feed = data.get('feed', 'N/A')
            spot = data.get('spot', 'N/A')
            results[ticker] = {'as_of': as_of, 'feed': feed, 'spot': spot, 'ok': True}
    except Exception as e:
        results[ticker] = {'error': str(e), 'ok': False}

print(json.dumps(results, indent=2))