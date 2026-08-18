#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system')
from scripts.hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
now_et = now.astimezone().strftime('%H:%M %Z')
age_min = 499.1

message = f'''Cipher data health change
Checked: {now.isoformat()} ({now_et})

LIVE_OPTION_CHAINS: STALE - all 12 tickers ~{age_min:.1f} min old (threshold 15 min, 24/7 stream)
  NVDA: 499.5 min, MSFT: 499.5 min, AAPL: 499.4 min, AVGO: 499.4 min
  AMZN: 499.4 min, IBIT: 499.4 min, GOOGL: 499.3 min, TSLA: 499.3 min
  META: 499.3 min, MU: 499.2 min, AMD: 499.1 min, QQQ: 499.1 min
Last update: ~2026-08-14T20:08:38Z (16:08 ET, near market close Fri)

TRADIER: OK - 499 min old (expected, outside market hours, weekend)
GEX: OK - 484 min old (expected, outside market hours, weekend)
Current time: {now_et} (market closed, weekend)'''

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')