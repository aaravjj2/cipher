#!/usr/bin/env python3
"""Send Cipher data health alert for stale live option chains."""
import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system/scripts')
from hermes_delivery import send_hermes_message
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat()
message = f"""Cipher data health change
Checked: {now}

LIVE_OPTION_CHAINS: STALE - latest ~1762 minutes old (all 12 tickers)
Latest: NVDA 2026-08-14T20:08:13.739907+00:00, MSFT 2026-08-14T20:08:15.417308+00:00, AAPL 2026-08-14T20:08:16.793107+00:00, AVGO 2026-08-14T20:08:18.227189+00:00, AMZN 2026-08-14T20:08:19.387373+00:00
Missing:

Note: Live option chains are a 24/7 stream - staleness is actionable even off-hours.
Tradier equity stream: off-hours (expected, market closed since Fri ~20:00 UTC)
GEX snapshots: off-hours (expected, market closed since Fri ~20:00 UTC)
Live option chains capture job appears stopped since Friday 2026-08-14 20:08 UTC close."""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
