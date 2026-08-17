#!/usr/bin/env python3
from scripts.hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now_utc = datetime.now(timezone.utc)
now_et = now_utc.astimezone().strftime('%a %b %d %H:%M:%S %Z %Y')

message = f"""Cipher data health change
Checked: {now_utc.isoformat()} ({now_et})

LIVE_OPTION_CHAINS: STALE - all 12 tickers ~1686 min old (threshold 15 min, 24/7 stream)
  NVDA: 1686.9 min, MSFT: 1686.8 min, AAPL: 1686.8 min, AVGO: 1686.8 min
  AMZN: 1686.8 min, IBIT: 1686.7 min, GOOGL: 1686.7 min, TSLA: 1686.7 min
  META: 1686.6 min, MU: 1686.6 min, AMD: 1686.5 min, QQQ: 1686.4 min
Last update: ~2026-08-14T20:08Z (16:08 ET, near market close Friday)

TRADIER: OK - 1695 min old (expected, outside market hours/weekend)
GEX: OK - 1671 min old (expected, outside market hours/weekend)
Current time: 20:15 ET Saturday (market closed, weekend)"""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
