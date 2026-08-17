# Phase 1 Audit — Trust and Daily Usefulness

Date: 2026-08-14 UTC / 2026-08-13 ET

## Gate summary

Phase 1 is functionally complete. The browser now opens on a Morning Brief,
exposes shared session/freshness state, shows all six shadow portfolios, serves a
truthful option-flow source when captured, reports a clearly labelled fallback
when it is not, uses a current validated universe, and publishes static builds as
one atomic directory exchange.

This audit was run while the US market was closed. Last-session semantics were
therefore tested live; regular-session transitions are covered deterministically
in unit tests and require a final observational check after the next cash open.

## Data truth audit

### Options flow

- Added a normalized `tradier_option_timesales` projection tied to immutable raw
  stream-event IDs.
- Backfilled only the latest captured session: 1,537,538 events, 18 underlyings,
  2026-08-13 only.
- SPY, AAPL, and MU live API samples all returned `tradier_stream` /
  `event_timesales`, one session date, and `as_of == max(included event time)`.
- DIA, which was outside the captured contract universe, returned the explicit
  `alpaca_chain_snapshot` / `latest_trade_per_contract` fallback. Its response
  retained only the newest represented date and warned that snapshot bid/ask is
  not an event-time aggressor label.
- `type=call&side=buy` returned only calls classified as buys. The prior backend
  accepted plural/internal values while the browser sent singular values; that
  silent filter failure is fixed.
- Event-time side classification preserves inside-spread prints as `unknown`.
  Missing classification is not converted into a buy or sell.
- Partial SQLite integrity check for the projection returned `ok`.

Measured closed-session latency during concurrent Parquet verification:

| Endpoint | Observed latency |
|---|---:|
| SPY captured flow, >= $50K | 1.1–2.2 s |
| AAPL captured flow, >= $50K | 0.2–0.6 s |
| DIA chain fallback, >= $50K | 2.2 s |

The SPY projection remains I/O-bound because it shares the 59 GB raw capture
database. This is acceptable for Phase 1 but is a Phase 4 storage-boundary target.

## Freshness and session audit

- The shared contract distinguishes `current`, `last_session`, `stale`, and
  `unavailable`.
- Session boundaries use America/New_York, including premarket, RTH, postmarket,
  closed, weekday, and UTC-to-ET date rollover behavior.
- Header and Morning Brief expose quote, flow, GEX, universe, research-ranking,
  and paper-monitor state before a user interprets a setup.
- At the closed-session live check, quote, flow, GEX, universe, research ranking,
  and paper monitor all correctly reported `last_session` rather than `live`.
- The audit found a misplaced `_scalar` block that made GEX look unavailable.
  It was corrected and a direct SQLite clock-read regression test was added.
- The shadow monitor had no in-session evaluation rows yet. Its freshness clock
  now says `monitor initialized; no in-session evaluation run recorded yet`
  rather than presenting this expected zero-history condition as missing data.

## Morning Brief audit

The default panel now assembles:

- SPY/QQQ/IWM context with provider timestamps;
- browser watchlist movers, tolerating individual quote failures;
- significant flow for the active ticker with source, session, newest event, and
  caveat;
- active-ticker GEX change with the public-OI/dealer-positioning caveat;
- latest saved scanner runs;
- configured alert state;
- manual holdings summary;
- all six shadow portfolio summaries; and
- data-health exceptions.

The live AAPL response contained 3 index rows, 7 significant prints, all 6 paper
portfolios, a valid two-snapshot GEX comparison, and no configured alerts or
manual holdings. Empty states remain truthful rather than manufactured.

Navigation links take the user from brief rows to the relevant ticker/panel.
Responsive grid breakpoints and partial/loading states passed lint, TypeScript,
and production export. A real-browser visual regression suite remains a Phase 4
item; Phase 1 did not claim pixel-level mobile verification.

## Paper portfolio audit

- Read-only API/UI exposes strategy rules, balances, realized P/L, records,
  positions, entry/exit fills, exit reasons, recent signals, dispositions,
  rejection reasons, run history, and equity curves.
- Live state is six portfolios, $600,000 combined starting/equity balance, zero
  closed trades, and zero open positions. This is correct: the monitor was
  installed after the prior qualifying period and deliberately never backfills
  missed prospective signals.
- Payload asserts `paper_only=true`, `read_only=true`, and
  `execution_capability=false`.
- No browser action can stage or submit an order.

## Universe audit

- Revalidated the prior 580-name cap-tier list against Alpaca's active US asset
  catalog and `has_options` attribute.
- 559 remain validated as of 2026-08-14.
- All 21 removals retain ticker, prior tier, reason, validation timestamp,
  provider, criterion, and an explicit no-auto-add policy for symbols lacking a
  cap classification.
- Universe age and lineage are returned by `/api/scan/universe` and the shared
  freshness contract.

## Deployment and operations audit

- `web/out` is staged and manifest-checked before publication.
- Linux `renameat2(RENAME_EXCHANGE)` atomically swaps complete directory trees;
  a test proves the live path sees the complete new tree while the staging path
  receives the complete old tree.
- The last three releases are retained for `--rollback`.
- `app/public` and `web/out` report `In sync` after deployment.
- Core and web services restarted cleanly with no warning-or-higher journal
  entries during the audit.
- Disk: 246 GB total, 155 GB used, 82 GB available (66%). Largest assets remain
  the 59 GB Tradier SQLite database, 20 GB raw Tradier JSONL, 9.8 GB historical
  options, and 7.4 GB live chains.
- Quick checks returned `ok` for GEX history, paper portfolios, alerts, and the
  timesales projection.
- The pre-existing Parquet retention service had failed because DuckDB was not
  available to that run. DuckDB 1.5.5 is now present in the service's configured
  Cipher environment, the failed state was reset, and a verified append-only
  rerun was started. Source SQLite is never pruned by this job.

## Verification evidence

- Python: 847 passed, 2 skipped.
- Browser server: 18/18 Node tests passed.
- Web source tests: 41/41 passed.
- ESLint passed.
- TypeScript passed.
- Next.js production build/export passed.
- Python compileall and Node syntax checks passed.
- Live API smoke passed for health, status, Morning Brief, flow, paper
  portfolios, and scanner universe.
- Static release manifest comparison passed.

## Known Phase 2 prerequisites

1. Observe freshness transitions and the six portfolio monitor during the next
   real RTH session; prospective zero-history cannot be replaced by a backfill.
2. A conventional option chain needs explicit executable-price, spread, age,
   Greek, IV, volume, OI-date, and unknown-field semantics.
3. Multi-leg payoff/risk math needs independent fixtures before being exposed.
4. The shared 59 GB flow/raw database is adequate but not the final low-latency
   query boundary.

## Gate verdict

PASS for Phase 1 product behavior, data truth, tests, and deployment. The running
append-only Parquet verification is an operational follow-up; its result must be
recorded before the Phase 2 audit gate.
