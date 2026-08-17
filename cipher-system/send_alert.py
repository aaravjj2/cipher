#!/usr/bin/env python3
from scripts.hermes_delivery import send_hermes_message

message = """Cipher data health change
Checked: 2026-08-14T17:28:23+00:00

GEX: STALE - per-ticker freshness check
  IBIT: 32.7 min old (threshold 15 min)
  IWM: 35.9 min old (threshold 15 min)

LIVE_OPTION_CHAINS: STALE - all 12 tickers ~7 min old (threshold 5 min during market hours)
  NVDA: 7.0 min, MSFT: 7.0 min, AAPL: 7.0 min, AVGO: 6.9 min
  AMZN: 6.9 min, IBIT: 6.9 min, GOOGL: 6.9 min, TSLA: 6.8 min
  META: 6.8 min, MU: 6.7 min, AMD: 6.7 min, QQQ: 6.6 min

TRADIER: OK - 0.0 min old
Current time: 13:28 ET (market hours)"""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
