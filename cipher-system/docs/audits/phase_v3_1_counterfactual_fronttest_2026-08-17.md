# Phase V3.1 audit — counterfactual fronttest ledger

Date: 2026-08-17 UTC

## Outcome

Every detected signal now receives a separate underlying-path outcome record,
including signals blocked by an open position, daily limit, contract liquidity,
allocation, or quote availability. The ledger stores target/invalidation or
fixed-horizon outcome, resolution time, bars observed, MFE, MAE, and the exact
methodology label. It never invents an option contract, fill, or option P/L.

## Existing-session reconciliation

The Aug 14 history was reconciled from captured signals and Alpaca underlying
bars: 15 signals resolved, with 5 targets, 9 invalidations, and 1 session expiry.
Among blocked signals, 3 later reached target and 5 invalidated; the fifth
invalidation includes the blocked QQQ VALIDATED signal in addition to four
blocked QQQ EARLY signals.

## Verification

- Fronttest, presentation, and daily-report focused suite: 12 passed.
- Conservative same-bar ordering is covered explicitly for one-minute pivots.
- V6 retains its existing target-touch then confirmed-close invalidation order.
- The reconciliation command opens no simulated position and calls no broker.

Phase verdict: accepted.
