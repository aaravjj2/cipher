# Phase V5.4 Audit — Options-Flow Freshness

Date: 2026-08-17
Scope: options-flow freshness contract and trader-facing tape status.

## Changes

- Added explicit `freshness.status` and `freshness.age_seconds` to `/api/flow` and `/api/spyglass` responses for both captured event timesales and chain-snapshot fallback.
- Current means the newest represented flow event is no more than 120 seconds old; older data is stale; absent event clocks are unknown.
- Added freshness tests for captured event tapes with current/stale/unknown behavior.
- Flow Tape now shows the freshness state and event age beside the session/source metadata.
- Existing caveats remain visible: chain fallback is one latest trade per contract, not a historical tape, and side inference may be unreliable.

## Verification

| Check | Result |
|---|---|
| Full Python suite | 962 passed, 2 skipped |
| Frontend node tests | 54 passed |
| ESLint/typecheck/build | passed |
| Core/web deployment | active after restart |

## Follow-up

The AAPL flow fallback can still be slow when no captured Tradier session exists and the Alpaca chain path is cold. This is now a visible freshness/data-availability issue rather than a false live tape. Phase V5.5 closes that follow-up with a bounded asynchronous refresh; see `phase_v5_5_response_budget_2026-08-17.md`.
