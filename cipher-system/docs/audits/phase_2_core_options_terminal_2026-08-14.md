# Phase 2 Audit — Core Options Terminal

Date: 2026-08-14 UTC / 2026-08-14 ET

## Gate summary

Phase 2 is complete for a private, manual, research-only workflow. Cipher now has
a conventional OPRA-backed option chain, executable-side multi-leg analysis, a
manual stocks-and-options risk ledger, named server-side watchlists, reproducible
saved screens, broader server alerts, and an idempotent delivery ledger. No broker
client, order route, order scheduler, or execution action was added.

## Option-chain provenance audit

- Calls and puts are paired by expiration and strike and retain provider symbol,
  feed, quote/trade time, bid, ask, midpoint, last, IV, all five Greeks, volume,
  OI, and OI date.
- Buys are priced at ask and sells at bid in the research builder. Midpoint is
  never substituted for an executable entry.
- Per-contract quote age, spread dollars/percent, moneyness, distance, intrinsic,
  extrinsic, and liquidity flags are visible.
- Missing OI or volume produces `OI_UNKNOWN` / `VOLUME_UNKNOWN`, and the combined
  liquidity verdict is `null`, not `true` or zero.
- Expected move is explicitly the nearest-strike call-plus-put midpoint. The 25
  delta skew and ATM term structure retain their input timestamps.
- IV rank and percentile remain `null` with
  `UNAVAILABLE_INSUFFICIENT_HISTORY`; no historical statistic is invented.
- OI remains provider-dated public OI. GEX alerts retain the public-OI heuristic
  caveat and never claim verified dealer positioning.

Live AAPL smoke returned OPRA, two expirations, 84 paired strike rows, and a
read-only contract in 1.08 seconds from a warm cache.

## Multi-leg accounting audit

Covered calls, cash-secured puts, long options, verticals, collars, and calendars
can be assembled from the chain. Results include debit/credit, expiration payoff,
max P/L, exact piecewise-linear breakevens, aggregate Greeks, per-structure risk,
liquidity warnings, and assignment/ex-dividend warnings.

The audit found and fixed two correctness failures before the gate:

1. sampled chart points were being reused as extrema, which could miss exact max
   P/L; extrema now come from zero/strike breakpoints and the slope at infinity;
2. naked short calls were reported as bounded-risk; a negative terminal upside
   slope now yields `max_loss_unbounded=true` and no numeric max loss.

Calendar/diagonal max P/L and breakevens remain unavailable because they require
a future-volatility model. The API states that caveat instead of treating the
near-leg expiry as a common terminal date.

## Portfolio-risk audit

- The manual ledger accepts stock and option legs, signed long/short quantity,
  entry price, fees, cash, notes, and strategy groups.
- Stock positions mark from the underlying quote. Options mark from current mid,
  falling back to last trade only with an explicit `mark_source`.
- Missing marks or Greeks remain `null` and generate exceptions.
- Aggregate Greeks, delta-dollar concentration, expiration buckets, short-contract
  counts, market value, P/L, and declared-cash net liquidation are available.
- CSV export/import round-trips the declared position fields atomically.
- This is not a broker statement. There is intentionally no account sync and no
  position close/order operation; corrections are manual ledger changes.

The empty live ledger returned in 2 ms and asserted
`execution_capability=false`.

## Watchlist, screener, and alert audit

- Watchlists are named and server-side. On first use the old browser Default list
  is migrated, and the selected named list is mirrored locally for the global
  ticker strip.
- Saved screens support price, day change, latest saved scanner score, and
  optionability. Each run returns its exact criteria, generated time, and ticker
  input set, making the result reproducible.
- Alert kinds now cover price/day change, saved scanner score, captured-session
  flow premium, bounded-window net GEX, nearest ATM IV/spread, nearest manual
  expiration, absolute manual-portfolio delta, and data-health exceptions.
- Unknown or stale metric observations do not clear/re-arm a rule.
- A SQLite delivery ledger is unique by rule, observed timestamp, threshold, and
  channel. Repeated insertion of the same crossing is idempotent.
- The five-minute server timer remains active; the browser evaluates only the four
  lightweight quote kinds and does not pretend to deliver the advanced kinds.

Known constraint: flow premium is the sum of prints included by the bounded flow
query. The metric carries `query_truncated` in its detail; truncation can cause a
false negative but is not extrapolated into a larger number. A no-watchlist saved
screen intentionally caps the optionable universe at 100 symbols to protect rate
limits; normal UI-created screens bind to a named list.

## Operations follow-up from Phase 1

The append-only Parquet mirror completed successfully. It fingerprint-verified all
13,649,211 Aug-13 events, wrote a 371,370,698-byte Parquet partition, matched row
count/id/time/hash fields round-trip, and left the 63 GB SQLite source untouched.
Service result and exit status are success/0.

## Verification evidence

- Python active suite: 856 passed, 2 skipped.
- Focused Phase 2 tests: option payoff, portfolio risk/CSV, watchlists/screens,
  alert storage/evaluation/deduplication all passed.
- Browser server: 18/18 Node tests passed.
- Web source tests: 41/41 passed.
- ESLint, TypeScript, Python compileall, Node syntax, and Next production export
  passed.
- Atomic static publish completed and manifest check reports `In sync`.
- Core and web services restarted with new PIDs and are active.
- Live smoke passed for option chain, option builder, portfolio risk, watchlists,
  alerts, advanced alert metric, core health, and authenticated web boundary.
- Alerts and watchlists SQLite `quick_check` both returned `ok`.
- A repository-root recursive pytest invocation was intentionally discarded: the
  outer workspace includes archived worktrees and quarantined vendor test suites.
  The authoritative run from `cipher-github/` produced the 856/2 result above.

## Remaining Phase 3 prerequisites

1. Charts need a unified, persistent workbench rather than isolated panel-specific
   SVGs and saves.
2. Journaling does not yet link positions, signals, saved chart state, thesis,
   targets, invalidation, MFE/MAE, and review outcomes.
3. Earnings/dividends/splits/economic events and a source-labelled fundamental
   profile are incomplete.
4. Ask Cipher is not yet grounded in the active workspace, truthful flow, option
   structure, portfolio-risk ledger, and cited timestamps.
5. Real-browser E2E and visual/mobile regression remain Phase 4 work.

## Gate verdict

PASS. Phase 2 accounting, provenance, unknown-data behavior, alert deduplication,
runtime health, and research-only boundaries are verified. Proceed to the linked
trader workflow in Phase 3.
