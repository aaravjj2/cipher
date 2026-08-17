#!/usr/bin/env python3
from scripts.hermes_delivery import send_hermes_message

message = """Cipher data health change
Checked: 2026-08-15T00:48:10+00:00 (Fri 20:48 ET)

LIVE_OPTION_CHAINS: STALE - all 12 tickers ~277 min old (threshold 15 min, 24/7 stream)
  NVDA: 277.2 min, MSFT: 277.2 min, AAPL: 277.2 min, AVGO: 277.1 min
  AMZN: 277.1 min, IBIT: 277.1 min, GOOGL: 277.1 min, TSLA: 277.0 min
  META: 277.0 min, MU: 276.9 min, AMD: 276.9 min, QQQ: 276.8 min
Last update: ~2026-08-14T20:08Z (16:08 ET, near market close)

TRADIER: OK - 287 min old (expected, outside market hours 7:30-17:00 ET)
GEX: OK - 263 min old (expected, outside market hours 9:30-16:10 ET)
Current time: 20:48 ET (market closed)"""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
