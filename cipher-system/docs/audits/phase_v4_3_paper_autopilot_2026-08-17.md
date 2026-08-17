# Phase V4.3 audit — staged paper autopilot and advisory model context

Date: 2026-08-17

## Outcome

Cipher now has an active, phase-aware **shadow autopilot**. It autonomously
creates premarket watch plans, requires a separate regular-session confirmation,
selects liquid options from Alpaca OPRA, simulates fills and exits, and records a
point-in-time learning corpus. It has no live-order code or broker client.

The first live confirmation cycle was intentionally blocked because the service
was enabled after the premarket planning windows. It recorded
`current_premarket_plan_missing` and opened zero positions. The first fully
eligible unattended cycle begins with the next 08:45 ET plan.

## State machine

| ET phase | Allowed action |
|---|---|
| 04:00–09:30 | Discovery and `WATCHLIST_ONLY` plan |
| 09:30–09:35 | Wait for the opening bar |
| 09:35–11:30 | Same-direction, fresh, triggered RTH confirmation |
| 11:30–15:45 | Monitor and exit only |
| 15:45–16:05 | Force-close window |
| Other/weekend | Closed/no entry |

Premarket candidates require valid geometry, sufficient option coverage, OPRA,
a score of at least 60, R/R of at least 1.5, and a frozen evidence ID. RTH entry
requires a second frozen evidence ID, regular-session context, current evidence,
sufficient coverage, and `agent_state=triggered` in the same direction.

## Execution simulation

- Data provider: local Cipher core → Alpaca SIP/OPRA.
- Indicative option fallback: hard block.
- Entry: ask plus configured slippage.
- Liquidation/exit mark: bid minus configured slippage.
- Contract: 1–3 DTE, no 0DTE, OI ≥100, volume ≥10, spread ≤12%, cost ≤$500.
- Portfolio: 3 concurrent, 1/ticker, 5 new/day, stop after 2 option-stop losses.
- Exit: underlying target/invalidation, +20%/-15% option P&L, 45 minutes, or
  15:45 ET. Overnight disabled.

The executor listens on loopback only. At audit time it was healthy in `shadow`
mode, reconciled, non-degraded, with zero open positions and no worker errors.

## Model layer

The pinned local CPU model `ProsusAI/finbert` revision
`4556d13015211d73dccd3fdd39d39232506f3e43` is installed and scheduled before
premarket planning. The first refresh processed 44 Yahoo documents across the
21-name context universe. SEC produced no recent documents, GDELT reported a
rate-limit skip, and unconfigured NewsAPI/Claude sources stayed explicitly
skipped.

FinBERT is advisory only and cannot change direction or authorize entry. FinGPT
is off. A future custom model is blocked until the book contains at least 100
closed replayable samples across 20 market dates with chronological date splits,
an embargo, and a ticker holdout. Current corpus: 0 samples / 0 dates.

## Operator surface

`/api/autopilot-status` and Morning Brief expose the phase, last scheduler
action, planned names, open shadow positions, executor health, model state, and
learning blockers. The page explicitly says there is no broker-order capability.

## Scheduling activated

- FinBERT context: 07:45 ET weekdays.
- Premarket plans: 08:45 and 09:15 ET weekdays.
- Confirmation: every five minutes from 09:35 through the 11:00 hour; the
  state machine rejects entries at/after 11:30.
- Dataset refresh: 16:20 ET weekdays.
- Executor: continuous, loopback-only shadow service.

## Verification

- Active Python suite: **951 passed, 2 skipped**.
- New/autopilot/paper safety slice: **74 passed**.
- TypeScript typecheck: passed.
- ESLint: passed.
- Next.js production export: passed and atomically published.
- Systemd unit/calendar verification: passed.
- Live Alpaca adapter probe: SIP underlying and OPRA option quotes normalized.
- Live pinned FinBERT smoke: positive/negative/neutral probabilities summed to 1.
- Authenticated Playwright: **14/14 passed** after aligning the cold-disk
  operator-status wait with the endpoint's read-only SQLite probes.

## Remaining gates

1. Observe the next full premarket → RTH cycle; today cannot be retroactively
   repaired without inventing a plan.
2. Accumulate actual prospective shadow outcomes before changing thresholds.
3. Re-audit after 20 sessions and again at 100 replayable outcomes.
4. Keep FinGPT off until it has a defined, testable role and an offline benchmark.
5. Live execution remains outside the project boundary and requires a separate,
   explicitly authorized package and human review.
