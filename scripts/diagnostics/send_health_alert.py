#!/usr/bin/env python3
"""Send Cipher data health alert for stale streams."""
import sys
sys.path.insert(0, '/home/aarav/Aarav/cipher/cipher-system/scripts')
from hermes_delivery import send_hermes_message

message = """Cipher data health alert — 2026-08-17T20:18:10+00:00 (post-market)

TRADIER EQUITY STREAM: STALE (18 min)
Last quotes: 2026-08-17T19:59:59Z (market close ~18 min ago)
Note: Tradier streaming stops at market close (20:00 UTC) — expected off-hours.

GEX SNAPSHOTS (Alpaca OPRA): STALE
Latest capture completed: 2026-08-17T19:18:50Z (~59 min ago)
Next capture should have run ~19:33, ~19:48, ~20:03 during market hours
All 15 tickers stale (26–124 min old)

LIVE OPTION CHAINS (24/7 Alpaca OPRA): MOSTLY OK
11/12 tickers fresh (10–13 min): NVDA, MSFT, AAPL, AVGO, IBIT, GOOGL, TSLA, META, MU, AMD, QQQ
1 STALE: AMZN (17.9 min, last 20:00:19Z)
Note: Live option chains run 24/7 — AMZN staleness is actionable.

ACTION: Check GEX capture loop (should run every 15 min during 13:30–20:00 UTC). Check AMZN chain fetcher."""

rc = send_hermes_message(message, target='telegram')
print(f'Return code: {rc}')