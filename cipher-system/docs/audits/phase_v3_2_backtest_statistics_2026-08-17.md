# Phase V3.2 audit — serially aware backtest uncertainty

Date: 2026-08-17 UTC

## Outcome

Standalone bar backtests now report both the existing deterministic IID
percentile-bootstrap interval and a deterministic circular moving-block
bootstrap interval. The second interval preserves short clusters of consecutive
trade outcomes instead of assuming every trade is independent.

The versioned experiment specification records both methods, 1,000 repeats, the
seed, and a five-trade block length. Invalid block sizes fail explicitly and
small samples remain blocked rather than receiving a confidence interval.

## Verification

- Backtest protocol/engine/filter suite: 27 passed.
- Deterministic rerun, serial-method metadata, small-sample blocker, and invalid
  block-length behavior are covered.
- TypeScript recognizes both uncertainty contracts.
- Timing, holdout, embargo, cost, and research-only rules are unchanged.

Phase verdict: accepted.
