#!/usr/bin/env python3
"""Send Cipher data health alert for stale live option chains."""

import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system/scripts')
from hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat(timespec='seconds')
message = f"""Cipher data health change
Checked: {now}

LIVE_OPTION_CHAINS: STALE - latest ~302-309 minutes old (all 12 tickers)
Latest: NVDA 2026-08-17T20:05:05.435299+00:00, MSFT 2026-08-17T20:05:07.145091+00:00, AAPL 2026-08-17T20:05:47.506006+00:00, AVGO 2026-08-17T20:05:50.142752+00:00, AMZN 2026-08-17T20:00:19.293046+00:00

Note: Live option chains are a 24/7 stream - staleness is actionable even off-hours.
Tradier equity stream: off-hours (expected, market closed since Mon ~20:00 UTC)
GEX snapshots: off-hours (expected, market closed since Mon ~20:00 UTC)
Live option chains capture job appears stopped since Monday 2026-08-17 20:07 UTC close."""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')