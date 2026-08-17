#!/usr/bin/env python3
from scripts.hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
now_et = now.astimezone().strftime("%H:%M %Z")
last_update = "2026-08-14T20:08:38.720385+00:00"
age_min = 391.8

message = f"""Cipher data health change
Checked: {now.isoformat()} ({now_et})

LIVE_OPTION_CHAINS: STALE - all 12 tickers ~{age_min:.1f} min old (threshold 15 min, 24/7 stream)
  NVDA: 392.2 min, MSFT: 392.2 min, AAPL: 392.2 min, AVGO: 392.1 min
  AMZN: 392.1 min, IBIT: 392.1 min, GOOGL: 392.1 min, TSLA: 392.0 min
  META: 392.0 min, MU: 391.9 min, AMD: 391.9 min, QQQ: 391.8 min
Last update: ~{last_update} (16:08 ET, near market close)

TRADIER: OK - 400.4 min old (expected, outside market hours 7:30-17:00 ET)
GEX: OK - 389.0 min old (expected, outside market hours 9:30-16:10 ET)
Current time: {now_et} (market closed)"""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
