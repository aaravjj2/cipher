# Legacy Capture Proxy (not current-autopilot performance)

Generated 2026-08-19 from the archived browser capture JSON under the local runtime.

This is included only to show the available multi-day historical coverage. It is **not** an exact replay of the current paper autopilot: these captures predate the current evidence/session contract, include multiple scanner families, and do not contain the autopilot's premarket-plan and RTH-confirmation lifecycle.

## Results

The existing capture replay used:

- 10-minute duplicate cooldown
- 45-minute capture-path horizon
- underlying spot snapshots
- cluster quad/top-5 filter
- ATM debit-spread proxy for illustrative P/L only

| Date | Observations | Entries | Wins | Losses | Win rate | Spread proxy P/L | Long-option reference P/L |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-07-27 | 2 | 2 | 0 | 2 | 0.00% | $0.00 | $0.00 |
| 2026-07-28 | 10,856 | 415 | 219 | 196 | 52.77% | $2,540.00 | $4,212.75 |
| 2026-07-29 | 5,024 | 400 | 211 | 189 | 52.75% | $3,386.53 | $2,826.92 |
| 2026-07-30 | 4,810 | 344 | 159 | 185 | 46.22% | -$545.43 | -$3,704.49 |
| 2026-07-31 | 8,812 | 694 | 345 | 349 | 49.71% | $481.38 | -$5,827.85 |
| 2026-08-03 | 10,304 | 571 | 317 | 254 | 55.52% | $5,704.20 | $9,433.80 |
| 2026-08-04 | 1,945 | 158 | 75 | 83 | 47.47% | $553.97 | $682.74 |
| 2026-08-05 | 4,496 | 528 | 267 | 261 | 50.57% | $3,387.78 | $4,623.18 |
| 2026-08-06 | 4,636 | 393 | 160 | 233 | 40.71% | $92.96 | -$1,060.76 |
| 2026-08-07 | 5,606 | 552 | 281 | 271 | 50.91% | $3,728.61 | $4,291.98 |
| 2026-08-10 | 501 | 60 | 23 | 37 | 38.33% | $278.37 | $472.72 |
| **Total** | — | **4,117** | **2,057** | **2,060** | **49.96%** | **$19,608.37** | **$15,950.99** |

## Why this must not be called autopilot performance

- The current autopilot requires a valid premarket `cipher` scan with current OPRA evidence, followed by a fresh regular-session `flash_agentic` confirmation in the same direction.
- The legacy captures do not carry that complete contract, so the current autopilot cannot be replayed faithfully on those dates.
- The proxy does not enforce the current paper executor's historical contract selection, quote age, spread, open-interest, cost, portfolio, or option stop/take-profit gates.
- Missing fields are not treated as zero, but the proxy's option values are model assumptions rather than historical fills.
- No orders were placed; this is read-only research output.

Use `autopilot_replay_2026-08-19.md` for the exact current-contract result.
