#!/usr/bin/env python3
"""Send current Cipher data health alert for stale live option chains."""
import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system/scripts')
from hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat(timespec='seconds')

# Calculate age from latest live option chains (QQQ is the last one)
latest_time = '2026-08-14T20:08:38.720385+00:00'
from datetime import datetime as dt
latest_dt = dt.fromisoformat(latest_time.replace('Z', '+00:00'))
now_dt = datetime.now(timezone.utc)
age_minutes = (now_dt - latest_dt).total_seconds() / 60

message = f"""Cipher data health change
Checked: {now}

LIVE_OPTION_CHAINS: STALE - latest {age_minutes:.1f} minutes ago (threshold <15 min)
Latest: NVDA 2026-08-14T20:08:13.739907+00:00, MSFT 2026-08-14T20:08:15.417308+00:00, AAPL 2026-08-14T20:08:16.793107+00:00, AVGO 2026-08-14T20:08:18.227189+00:00, AMZN 2026-08-14T20:08:19.387373+00:00, IBIT 2026-08-14T20:08:20.442456+00:00, GOOGL 2026-08-14T20:08:22.265485+00:00, TSLA 2026-08-14T20:08:24.382923+00:00, META 2026-08-14T20:08:27.035445+00:00, MU 2026-08-14T20:08:31.587911+00:00, AMD 2026-08-14T20:08:34.300548+00:00, QQQ 2026-08-14T20:08:38.720385+00:00
All 12 scanner tickers stale since Friday 2026-08-14 20:08 UTC close. Live option chains are a 24/7 stream — capture job appears stopped.

Note: Tradier equity stream and GEX snapshots are off-hours (expected, market closed since Fri ~20:00 UTC)."""

rc = send_hermes_message(message, target='telegram')
print(f'Send result: {rc}')
