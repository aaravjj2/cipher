#!/usr/bin/env python3
import sys
sys.path.insert(0, 'scripts')
from hermes_delivery import send_hermes_message

message = """Cipher data health change
Checked: 2026-08-16T01:15:00+00:00

LIVE_OPTION_CHAINS: STALE - latest 1743.2 minutes ago (threshold <15 min)
Latest: NVDA 2026-08-14T20:08:13.739907+00:00, MSFT 2026-08-14T20:08:15.417308+00:00, AAPL 2026-08-14T20:08:16.793107+00:00, AVGO 2026-08-14T20:08:18.227189+00:00, AMZN 2026-08-14T20:08:19.387373+00:00
All 12 scanner tickers stale since Friday 20:08 UTC close. Live option chains are a 24/7 stream — capture job appears stopped."""

rc = send_hermes_message(message, target='telegram')
print(f'Send result: {rc}')
