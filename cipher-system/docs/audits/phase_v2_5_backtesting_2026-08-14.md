# Phase V2.5 audit — backtesting and experiment protocol

Date: 2026-08-14 UTC
Boundary: research simulation only; no broker client, order route, or live authority

## Outcome

The browser-launched signal backtest now runs under a versioned evidence
contract instead of an implicit collection of engine defaults. Every run locks
its parameters before execution, names slippage and commission separately,
purges a chronological train/holdout boundary, uses deterministic controls and
bootstrap sampling, applies a cross-symbol portfolio-capacity model, and saves
the exact bar input and report locally.

## Implemented

- Added `core/backtest_protocol.py` with:
  - stable canonical parameter hashes;
  - immutable timing/fill rules;
  - explicit slippage and commission inputs, charged on both sides;
  - chronological train/holdout splitting with a boundary embargo;
  - per-symbol and aggregate SHA-256 data fingerprints;
  - deterministic bootstrap 95% intervals;
  - fixed-fraction portfolio accounting with a maximum concurrent-position cap;
  - an explicit `RESEARCH_ONLY` / `live_order_authority: false` manifest.
- Upgraded background jobs to evaluate full, training, and locked holdout
  partitions and to use the same matched random-entry control in each eligible
  partition.
- Persisted an exact compressed input snapshot and JSON report under
  `data/backtest_runs/`, using atomic replacement.
- Added a downloadable standalone trade ledger containing entry/exit time,
  fill price, reason, return, MFE, MAE, and signal index.
- Added UI controls for history, slippage, commission, and holdout size plus a
  locked-experiment summary, data fingerprint, train/holdout cards, uncertainty,
  and constrained portfolio result.

## Adversarial gates

- Signal on bar N fills bar N+1 open.
- If stop and target are both touched in one bar, the stop wins.
- Higher costs reduce return by the exact two-sided charge.
- Train and holdout are chronological and separated by purged bars.
- Identical specs produce identical hashes; parameter changes change the hash.
- Product experiment specs reject omitted costs.
- Bootstrap and random controls are deterministic for a fixed seed.
- Capacity-constrained portfolio accounting skips overlapping trades beyond the
  configured limit.

## Live bounded verification

NVDA + AAPL, 15-minute bars, one year, EOD Focus, 2 bps slippage/side,
0 commission, 30% holdout, seed 17:

- parameter lock: `213548505b6cc1a0...`
- run/data lock: `8894bd09aad02bf7...` / `1f6e1f3c5d74845d...`
- full: 103 trades, -0.0657% average per trade;
- locked holdout: 34 trades, 44.1% wins, -0.0264% average, 0.893 profit factor;
- holdout bootstrap 95% interval: -0.1849% to +0.1470%, includes zero;
- constrained $100,000 portfolio: $99,324.28 ending equity, -$675.72;
- exact input snapshot and report both existed after the run.

This is negative/indeterminate evidence, not a candidate for promotion.

## Verification

- focused protocol/engine/filter tests: 25 passed;
- complete active Python suite: 883 passed, 2 skipped;
- web source tests: 51 passed;
- TypeScript typecheck and ESLint: passed;
- production build: passed;
- dependency audit: 0 vulnerabilities;
- authenticated Chromium: 8 scenarios passed after correcting the test's sidebar
  label from `Signal Backtest` to the actual `Backtest` label;
- core/web services and scheduled research timer remained active.

## Remaining limitations / next audit inputs

- The generic signal backtester models underlying bars, not option-premium fills.
  The options backtest remains a separate captured-data engine and must not be
  conflated with these equity results.
- Slippage is a fixed configured value unless a measured cost profile is used;
  it is not an intrabar market-impact model.
- The portfolio layer uses equal fixed-fraction sizing, not covariance-aware
  allocation or buying-power/margin rules.
- Bootstrap samples trades independently and therefore does not model clustered
  regime dependence. A block bootstrap is the next statistical upgrade.
- Filter-mode exposes controlled partitions but does not yet export its internal
  base-trade ledger or bootstrap each named partition.

Phase verdict: accepted for V2. The engine is materially harder to fool and its
limitations are visible rather than buried in defaults.
