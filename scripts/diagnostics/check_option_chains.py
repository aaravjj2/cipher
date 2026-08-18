#!/usr/bin/env python3
import json
from pathlib import Path

chain_dir = Path("/home/aarav/Aarav/cipher/runtime/data/live_option_chains")
tickers = ("NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ")

for ticker in tickers:
    latest_path = chain_dir / f"latest_{ticker}.json"
    if latest_path.is_file():
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
            as_of = payload.get("as_of") or payload.get("timestamp")
            print(f"{ticker}: as_of={as_of}")
        except Exception as e:
            print(f"{ticker}: ERROR - {e}")
    else:
        print(f"{ticker}: MISSING")