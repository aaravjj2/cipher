#!/usr/bin/env python3
"""Send Cipher data health alert for stale live option chains."""
import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system/scripts')
from hermes_delivery import send_hermes_message

message = """Cipher data health change
Checked: 2026-08-15T20:29:13+00:00

LIVE_OPTION_CHAINS: STALE - latest 1460.4 minutes old
Latest: NVDA 2026-08-14T20:08:13.739907+00:00, MSFT 2026-08-14T20:08:15.417308+00:00, AAPL 2026-08-14T20:08:16.793107+00:00, AVGO 2026-08-14T20:08:18.227189+00:00, AMZN 2026-08-14T20:08:19.387373+00:00
Missing:

Note: Live option chains are a 24/7 stream - staleness is actionable even off-hours.
Tradier equity stream: off-hours (expected, market closed ~29 min ago)
GEX snapshots: off-hours (expected, market closed ~29 min ago)"""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')
