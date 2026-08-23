# Paper Autopilot Historical Replay

Generated: `2026-08-19T15:21:48.770069+00:00`

## Result

- Exact-contract replay days: **2026-08-18**
- Unique confirmations: **5**
- Resolved wins/losses: **0 / 2**
- Resolved win rate: **0.0%**
- Unresolved: **3**
- Option P/L: **not calculated** (historical option marks unavailable)

## Day coverage

| Date | Status | Premarket scans | RTH confirmation scans |
|---|---|---:|---:|
| 2026-08-10 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-11 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-12 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-13 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-14 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-16 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-17 | legacy_or_missing_premarket_contract | 0 | 0 |
| 2026-08-18 | exact_current_contract | 2 | 23 |
| 2026-08-19 | legacy_or_missing_premarket_contract | 0 | 0 |

## Replayed entries

| Date | Ticker | Entry | Outcome | Directional move % |
|---|---|---|---|---:|
| 2026-08-18 | COIN | 2026-08-18T13:35:44.095078+00:00 | invalidation_hit | -0.4585 |
| 2026-08-18 | AMD | 2026-08-18T13:45:26.858378+00:00 | invalidation_hit | -1.0243 |
| 2026-08-18 | ASML | 2026-08-18T13:55:25.666052+00:00 | unresolved_capture_path | 0.4426 |
| 2026-08-18 | MDB | 2026-08-18T14:25:23.518249+00:00 | unresolved_no_future_snapshot | None |
| 2026-08-18 | APP | 2026-08-18T14:45:30.007928+00:00 | unresolved_capture_path | 0.0483 |

## Caveats

- Only scans carrying the current evidence/session contract are replayed as exact autopilot decisions.
- Repeated confirmations for one ticker are deduplicated to one candidate entry, matching the executor episode policy.
- Underlying target/invalidation outcomes use sparse regular-session snapshots; intrabar touches between snapshots remain unknown.
- Historical option contract selection, bid/ask fills, option take-profit, and option stop-loss are unavailable, so option P/L is not calculated.
- This is a paper/read-only research replay and does not place or route orders.
